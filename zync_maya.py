"""
Zync Maya Plugin

This Maya plugin implements the Zync Python API to provide an interface
for launching Maya jobs on Zync.

Depends on the zync-python Python API:

https://github.com/zync/zync-python

Usage:
  import zync_maya
  zync_maya.submit_dialog()

"""

__version__ = '1.0.4'

import copy
import hashlib
import math
import os
import platform
import re
import string
import sys
import time
import webbrowser
from functools import partial

if os.environ.get('ZYNC_API_DIR') and os.environ.get('ZYNC_MAYA_API_KEY'):
  API_DIR = os.environ.get('ZYNC_API_DIR')
  API_KEY = os.environ.get('ZYNC_MAYA_API_KEY')
else:
  config_path = '%s/config_maya.py' % (os.path.dirname(__file__),)
  if not os.path.exists(config_path):
    raise Exception('Could not locate config_maya.py, please create.')
  from config_maya import *

required_config = ['API_DIR', 'API_KEY']

for key in required_config:
  if not key in globals():
    raise Exception('config_maya.py must define a value for %s.' % (key,))

sys.path.append(API_DIR)
import zync
zync_conn = zync.Zync('maya_plugin', API_KEY, application='maya')

UI_FILE = '%s/resources/submit_dialog.ui' % (os.path.dirname(__file__),)

_XGEN_IMPORT_ERROR = None

import maya.cmds as cmds
import maya.mel
import maya.utils
# Attempt to import Xgen API. Log error on failure but continue, in
# case of older Maya version or if Xgen is simply unavailable for
# some reason.
try:
  import xgenm
except ImportError as e:
  _XGEN_IMPORT_ERROR = str(e)
  print 'Error loading Xgen API: %s' % _XGEN_IMPORT_ERROR

def eval_ui(path, type='textField', **kwargs):
  """
  Returns the value from the given ui element.
  """
  return getattr(cmds, type)(path, query=True, **kwargs)

def proj_dir():
  """
  Returns the Maya project directory of the current scene.
  """
  return cmds.workspace(q=True, rd=True)

def frame_range():
  """
  Returns the frame-range of the maya scene as a string, like:
    1001-1350
  """
  start = str(int(cmds.getAttr('defaultRenderGlobals.startFrame')))
  end = str(int(cmds.getAttr('defaultRenderGlobals.endFrame')))
  return '%s-%s' % (start, end)

def udim_range():
  bake_sets = list(bake_set for bake_set in cmds.ls(type='VRayBakeOptions') \
    if bake_set != 'vrayDefaultBakeOptions')
  u_max = 0
  v_max = 0
  for bake_set in bake_sets:
    conn_list = cmds.listConnections(bake_set)
    if conn_list == None or len(conn_list) == 0:
      continue
    uv_info = cmds.polyEvaluate(conn_list[0], b2=True)
    if uv_info[0][1] > u_max:
      u_max = int(math.ceil(uv_info[0][1]))
    if uv_info[1][1] > v_max:
      v_max = int(math.ceil(uv_info[1][1]))
  return '1001-%d' % (1001+u_max+(10*v_max))

def seq_to_glob(in_path):
  head = os.path.dirname(in_path)
  base = os.path.basename(in_path)
  match = list(re.finditer('\d+', base))[-1]
  new_base = '%s*%s' % (base[:match.start()], base[match.end():])
  return '%s/%s' % (head, new_base)

def _file_handler(node):
  """Returns the file referenced by a Maya file node. Returned files may
  contain wildcards when they reference image sequences, for example an
  animated texture node, or a path containing <UDIM> token."""
  # If the path contains a <UDIM> token, use computedFileTextureNamePattern,
  # it preserves the token.
  if (cmds.attributeQuery('computedFileTextureNamePattern', node=node, exists=True) and
      '<udim>' in cmds.getAttr('%s.computedFileTextureNamePattern' % node).lower()):
    texture_path = cmds.getAttr('%s.computedFileTextureNamePattern' % node)
  else:
    texture_path = cmds.getAttr('%s.fileTextureName' % node)
  try:
    if cmds.getAttr('%s.useFrameExtension' % (node,)) == True:
      out_path = seq_to_glob(texture_path)
    else:
      out_path = texture_path
    out_path = re.sub('<udim>|<tile>', '*', out_path, flags=re.IGNORECASE)
    yield out_path
    arnold_use_tx = False
    try:
      arnold_use_tx = cmds.getAttr('defaultArnoldRenderOptions.use_existing_tiled_textures')
    except:
      arnold_use_tx = False
    if arnold_use_tx:
      head, ext = os.path.splitext(out_path)
      tx_path = '%s.tx' % (head,)
      if os.path.exists(tx_path):
        yield tx_path
  except:
    yield texture_path

def _cache_file_handler(node):
  """Returns the files references by the given cacheFile node"""
  path = cmds.getAttr('%s.cachePath' % node)
  cache_name = cmds.getAttr('%s.cacheName' % node)

  yield '%s/%s.mc' % (path, cache_name)
  yield '%s/%s.mcx' % (path, cache_name)
  yield '%s/%s.xml' % (path, cache_name)

def _diskCache_handler(node):
  """Given a diskCache node, returns path of cache file it
  references.

  Args:
    node: str, name of diskCache node

  Yields:
    tuple of str, paths referenced 
  """
  cache_name = cmds.getAttr('%s.cacheName' % node)
  # if its an absolute path we're done, otherwise we need to resolve it
  # via project settings
  if os.path.isabs(cache_name):
    yield cache_name
  else:
    disk_cache_dir = cmds.workspace(fileRuleEntry='diskCache')
    if not disk_cache_dir:
      print 'WARNING: disk cache path not found. assuming data/'
      disk_cache_dir = 'data'
    # resolve relative paths with the main project path
    if not os.path.isabs(disk_cache_dir):
      disk_cache_dir = os.path.join(cmds.workspace(q=True, rd=True),
                                    disk_cache_dir)
    yield os.path.join(disk_cache_dir, cache_name)

def _vrmesh_handler(node):
  """Handles vray meshes"""
  yield cmds.getAttr('%s.fileName' % node)

def _mrtex_handler(node):
  """Handles mentalrayTexutre nodes"""
  yield cmds.getAttr('%s.fileTextureName' % node)

def _gpu_handler(node):
  """Handles gpuCache nodes"""
  yield cmds.getAttr('%s.cacheFileName' % node)

def _mrOptions_handler(node):
  """Handles mentalrayOptions nodes, for Final Gather"""
  mapName = cmds.getAttr('%s.finalGatherFilename' % node).strip()
  if mapName != "":
    path = cmds.workspace(q=True, rd=True)
    if path[-1] != "/":
      path += "/"
    path += "renderData/mentalray/finalgMap/"
    path += mapName
    #if not mapName.endswith(".fgmap"):
    #   path += ".fgmap"
    path += "*"
    yield path

def _mrIbl_handler(node):
  """Handles mentalrayIblShape nodes"""
  yield cmds.getAttr('%s.texture' % node)

def _abc_handler(node):
  """Handles AlembicNode nodes"""
  yield cmds.getAttr('%s.abc_File' % node)

def _vrSettings_handler(node):
  """Handles VRaySettingsNode nodes, for irradiance map"""
  irmap = cmds.getAttr('%s.ifile' % node)
  if cmds.getAttr('%s.imode' % node) == 7:
    if irmap.find('.') == -1:
      irmap += '*'
    else:
      last_dot = irmap.rfind('.')
      irmap = '%s*%s' % (irmap[:last_dot], irmap[last_dot:])
  yield irmap
  yield cmds.getAttr('%s.fnm' % node)

def _particle_handler(node):
  project_dir = cmds.workspace(q=True, rd=True)
  if project_dir[-1] == '/':
    project_dir = project_dir[:-1]
  if node.find('|') == -1:
    node_base = node
  else:
    node_base = node.split('|')[-1]
  path = None
  try:
    startup_cache = cmds.getAttr('%s.scp' % (node,)).strip()
    if startup_cache in (None, ''):
      path = None
    else:
      path = '%s/particles/%s/%s*' % (project_dir, startup_cache, node_base)
  except:
    path = None
  if path == None:
    scene_base, ext = os.path.splitext(os.path.basename(cmds.file(q=True, loc=True)))
    path = '%s/particles/%s/%s*' % (project_dir, scene_base, node_base)
  yield path

def _ies_handler(node):
  """Handles VRayLightIESShape nodes, for IES lighting files"""
  yield cmds.getAttr('%s.iesFile' % node)

def _fur_handler(node):
  """Handles FurDescription nodes"""
  #
  #  Find all "Map" attributes and see if they have stored file paths.
  #
  for attr in cmds.listAttr(node):
    if attr.find('Map') != -1 and cmds.attributeQuery(attr, node=node, at=True) == 'typed':
      index_list = ['0', '1']
      for index in index_list:
        try:
          map_path = cmds.getAttr('%s.%s[%s]' % (node, attr, index))
          if map_path != None and map_path != '':
            yield map_path
        except:
          pass

def _ptex_handler(node):
  """Handles Mental Ray ptex nodes"""
  yield cmds.getAttr('%s.S00' % node)

def _substance_handler(node):
  """Handles Vray Substance nodes"""
  yield cmds.getAttr('%s.p' % node)

def _imagePlane_handler(node):
  """Handles Image Planes"""
  # only return the path if the display mode is NOT set to "None"
  if cmds.getAttr('%s.displayMode' % (node,)) != 0:
    texture_path = cmds.getAttr('%s.imageName' % (node,))
    try:
      if cmds.getAttr('%s.useFrameExtension' % (node,)) == True:
        yield seq_to_glob(texture_path)
      else:
        yield texture_path
    except:
      yield texture_path

def _mesh_handler(node):
  """Handles Mesh nodes, in case they are using MR Proxies"""
  try:
    proxy_path = cmds.getAttr('%s.miProxyFile' % (node,))
    if proxy_path != None:
      yield proxy_path
  except:
    pass

def _dynGlobals_handler(node):
  """Handles dynGlobals nodes"""
  project_dir = cmds.workspace(q=True, rd=True)
  if project_dir[-1] == '/':
    project_dir = project_dir[:-1]
  cache_dir = cmds.getAttr('%s.cd' % (node,))
  if cache_dir not in (None, ''):
    path = '%s/particles/%s/*' % (project_dir, cache_dir.strip())
    yield path

def _aiStandIn_handler(node):
  """Handles aiStandIn nodes"""
  path = cmds.getAttr('%s.dso' % (node,))
  # change frame reference to wildcard pattern
  path = re.sub('#+', '*', path)
  yield path

def _aiImage_handler(node):
  """Handles aiImage nodes"""
  yield cmds.getAttr('%s.filename' % node)

def _aiPhotometricLight_handler(node):
  """Handles aiPhotometricLight nodes"""
  yield cmds.getAttr('%s.aiFilename' % node)

def _exocortex_handler(node):
  """Handles Exocortex Alembic nodes"""
  yield cmds.getAttr('%s.fileName' % node)

def _vrayPtex_handler(node):
  yield cmds.getAttr('%s.ptexFile' % node)

def _ribArchive_handler(node):
  """Handles RIB archive nodes"""
  archive_path = cmds.getAttr('%s.filename' % node)
  yield archive_path
  # determine the name of the RIB archive directory, which matches part
  # of the basename of the archive path. e.g.:
  #   archive1.zip: archive name = "archive1"
  #   archive1.${F4}.rib: archive name also = "archive1"
  # "rman subst" resolves all placeholders such as frame number. if the
  # resolved name differs from the original, we assume there's a frame
  # number chunk at the end of the name we must also drop to arrive at
  # the archive name.
  if maya.mel.eval('rman subst "%s"' % archive_path) == archive_path:
    last_index = -1
  else:
    last_index = -2
  archive_name = '.'.join(os.path.basename(archive_path).split('.')[:last_index])
  # now we find the archive directory, which contains various materials
  # associated with the archive. there are a few scenarios...
  #
  # use case #1: the RIB archive lives within the archive directory
  if os.path.basename(os.path.dirname(archive_path)) == archive_name:
    archive_dir = os.path.dirname(archive_path)
  # use case #2: RIB archive is a sibling of the archive directory
  elif os.path.exists(os.path.join(os.path.dirname(archive_path), archive_name)):
    archive_dir = os.path.join(os.path.dirname(archive_path), archive_name)
  # use case #3: we couldn't find the archive directory. log a warning and just
  #              yield the archive itself.
  else:
    print 'WARNING: could not locate RIB archive directory for node %s' % node
    archive_dir = None
  # Zync prefers file paths rather than directories, so walk the archive
  # directory and add all child files to the file list
  if archive_dir:
    for current_dir, child_dirs, child_files in os.walk(archive_dir):
      for child_file in child_files:
        yield os.path.join(current_dir, child_file)

def get_scene_files():
  """Returns all of the files being used by the scene"""
  file_types = {
    'file': _file_handler,
    'cacheFile': _cache_file_handler,
    'diskCache': _diskCache_handler,
    'VRayMesh': _vrmesh_handler,
    'mentalrayTexture': _mrtex_handler,
    'gpuCache': _gpu_handler,
    'mentalrayOptions': _mrOptions_handler,
    'mentalrayIblShape': _mrIbl_handler,
    'AlembicNode': _abc_handler,
    'VRaySettingsNode': _vrSettings_handler,
    'particle': _particle_handler,
    'VRayLightIESShape': _ies_handler,
    'FurDescription': _fur_handler,
    'mib_ptex_lookup': _ptex_handler,
    'substance': _substance_handler,
    'imagePlane': _imagePlane_handler,
    'mesh': _mesh_handler,
    'dynGlobals': _dynGlobals_handler,
    'aiStandIn': _aiStandIn_handler,
    'aiImage': _aiImage_handler,
    'aiPhotometricLight': _aiPhotometricLight_handler,
    'ExocortexAlembicFile': _exocortex_handler,
    'VRayPtex': _vrayPtex_handler,
    'RenderManArchive': _ribArchive_handler,
  }

  for file_type in file_types:
    handler = file_types.get(file_type)
    nodes = cmds.ls(type=file_type)
    for node in nodes:
      for scene_file in handler(node):
        if scene_file:
          scene_file = scene_file.replace('\\', '/')
          print 'found file dependency from %s node %s: %s' % (file_type, node, scene_file)
          yield scene_file

  try:
    for xgen_file in get_xgen_files():
      yield xgen_file
  except NameError as e:
    print 'error retrieving Xgen file list: %s' % str(e)

def get_xgen_files():
  """Yield all Xgen file dependencies in the scene."""
  # Get collection list, if the call fails due to Xgen not being
  # loaded, stop.
  if _XGEN_IMPORT_ERROR:
    raise NameError('Xgen is not loaded due to error: %s' % _XGEN_IMPORT_ERROR)
  collection_list = xgenm.palettes()
  for collection in collection_list:
    for def_file in _get_xgen_collection_definition(collection):
      print 'found Xgen collection definition: %s' % def_file
      yield def_file
    for xgen_file in _get_xgen_collection_files(collection):
      print 'found Xgen collection file: %s' % xgen_file
      yield xgen_file

def _get_xgen_collection_definition(collection_name):
  """Yield Xgen collection direct dependencies.

  Args:
    collection_name: str, name of Xgen collection in the current scene

  Returns:
    Yields str for each definition files associated with that collection.
  """
  if _XGEN_IMPORT_ERROR:
    raise NameError('Xgen is not loaded due to error: %s' % _XGEN_IMPORT_ERROR)
  scene_dir, scene_basename = os.path.split(cmds.file(q=True, loc=True))
  scene_name, _ = os.path.splitext(scene_basename)
  # Xgen definition files must meet very specific conventions - they
  # must live in the same directory as the scene file and be named
  # according to a strict <scene name>__<collection name> format.
  # These are Xgen conventions, not specific to Zync.
  # Maya avoids using the namespace character ':' in filenames, so
  # we must do the same replacement.
  filenames = [
    '%s__%s.xgen' % (scene_name, collection_name.replace(':', '__')),
    '%s__%s.abc' % (scene_name, collection_name.replace(':', '__ns__')),
  ]
  for filename in filenames:
    yield os.path.join(scene_dir, filename).replace('\\', '/')

def _get_xgen_collection_files(collection_name):
  """Get Xgen indirect dependencies, specifically files stored
  in related objects."""
  if _XGEN_IMPORT_ERROR:
    raise NameError('Xgen is not loaded due to error: %s' % _XGEN_IMPORT_ERROR)
  xg_proj_path = xgenm.getAttr('xgProjectPath', collection_name)
  xg_data_path = xgenm.getAttr('xgDataPath', collection_name)
  xg_data_path = xg_data_path.replace('${PROJECT}', xg_proj_path)
  # upload all files under collection root
  for dir_name, subdir_list, file_list in os.walk(xg_data_path):
    for xg_file in file_list:
      if not xg_file.startswith('.'):
        yield os.path.join(dir_name, xg_file).replace('\\', '/')
  # search objects for files too
  for xg_desc in xgenm.descriptions(collection_name):
    for xg_obj in xgenm.objects(collection_name, xg_desc):
      for xg_file in _get_xgen_object_files(collection_name, xg_desc, xg_obj):
        yield xg_file

def _get_xgen_object_files(collection_name, desc_name, object_name):
  """Get all files linked to an Xgen object."""
  if _XGEN_IMPORT_ERROR:
    raise NameError('Xgen is not loaded due to error: %s' % _XGEN_IMPORT_ERROR)
  # Assume we only care if the object has a "files" attribute.
  if xgenm.attrExists('files', collection_name, desc_name, object_name):
    xg_proj_path = xgenm.getAttr('xgProjectPath', collection_name)
    # files attr has a rather strange format, which we must parse and attempt
    # to infer file paths from. For example:
    # #ArchiveGroup 0 name="stalagmite" thumbnail="stalagmite.png" description="No description." \
    #   materials="${PROJECT}/xgen/archives/materials/stalagmite.ma" color=[1.0,0.0,0.0]\n0 \
    #   "${PROJECT}/xgen/archives/abc/stalagmite.abc"
    for attr in re.findall(r'(?:[^\s,"]|"(?:\\.|[^"])*")+',
        xgenm.getAttr('files', collection_name, desc_name, object_name)):
      attr_split = attr.split('=')
      current_file = None
      if not attr_split:
        pass
      # Look for something that looks like a file path
      elif len(attr_split) < 2 and (os.sep in attr or '/' in attr):
        current_file = attr.strip('"').replace('${PROJECT}', xg_proj_path)
      # Also catch materials= tags.
      elif attr_split[0] == 'materials':
        current_file = attr_split[1].strip('"').replace('${PROJECT}', xg_proj_path)
      if current_file:
        yield current_file
        # If the file is a .gz archive, look for a toc file as well. Arnold archives
        # in particular often require this.
        if current_file.endswith('.gz'):
          head, _ = os.path.splitext(current_file)
          toc_path = head + 'toc'
          if os.path.exists(toc_path):
            yield toc_path

def get_default_extension(renderer):
  """
  Returns the filename prefix for the given renderer, either mental ray
  or maya software.
  """
  if renderer == 'sw':
    menu_grp = 'imageMenuMayaSW'
  elif renderer == 'mr':
    menu_grp = 'imageMenuMentalRay'
  else:
    raise Exception('Invalid Renderer: %s' % renderer)
  try:
    val = cmds.optionMenuGrp(menu_grp, q=True, v=True)
  except RuntimeError:
    msg = 'Please open the Maya Render globals before submitting.'
    raise Exception(msg)
  else:
    return val.split()[-1][1:-1]

LAYER_INFO = {}
def collect_layer_info(layer, renderer):
  cur_layer = cmds.editRenderLayerGlobals(q=True, currentRenderLayer=True)
  cmds.editRenderLayerGlobals(currentRenderLayer=layer)

  layer_info = {}

  # get list of active render passes
  layer_info['render_passes'] = []
  if (renderer == 'vray' and
    cmds.getAttr('vraySettings.imageFormatStr') != 'exr (multichannel)'
    and cmds.getAttr('vraySettings.relements_enableall') != False):
    pass_list = cmds.ls(type='VRayRenderElement')
    pass_list += cmds.ls(type='VRayRenderElementSet')
    for r_pass in pass_list:
      if cmds.getAttr('%s.enabled' % (r_pass,)) == True:
        layer_info['render_passes'].append(r_pass)

  # get prefix information
  if renderer == 'vray':
    node = 'vraySettings'
    attribute = 'fileNamePrefix'
  elif renderer in ('sw', 'mr', 'arnold'):
    node = 'defaultRenderGlobals'
    attribute = 'imageFilePrefix'
  try:
    layer_prefix = cmds.getAttr('%s.%s' % (node, attribute))
    layer_info['prefix'] = layer_prefix
  except Exception:
    layer_info['prefix'] = ''

  cmds.editRenderLayerGlobals(currentRenderLayer=cur_layer)
  return layer_info

def clear_layer_info():
  global LAYER_INFO
  LAYER_INFO = {}

def get_layer_override(layer, renderer, field):
  global LAYER_INFO
  if layer not in LAYER_INFO:
    LAYER_INFO[layer] = collect_layer_info(layer, renderer)
  return LAYER_INFO[layer][field]

def get_maya_version():
  """Returns the current major Maya version in use."""
  #
  # "about -api" returns a value containing both major and minor
  # maya versions in one integer, e.g. 201515. Divide by 100 to
  # find the major version.
  #
  return str(int(float(maya.mel.eval('about -api')) / 100))

def _rman_translate_format_to_extension(format):
  """Translate an image format to the extension of files it
  generates. For example, "openexr" becomes "exr".

  Args:
    format: str, the image format

  Returns:
    str, the output extension. If the format is unrecognized, the
    original image format will be returned.
  """
  # rman getPref returns a flat string where even items are format
  # names and odd indexes are file extensions. like:
  # "openexr exr softimage pic shader slo"
  formats_list = maya.mel.eval("rman getPref AssetnameExtTable;").split()
  # look for the format, then return the next item in the string
  # if the format isn't found, return it as is.
  try:
    format_index = formats_list.index(format)
  except ValueError:
    return format
  return formats_list[format_index+1]

class MayaZyncException(Exception):
  """
  This exception issues a Maya warning.
  """
  def __init__(self, msg, *args, **kwargs):
    cmds.warning(msg)
    super(MayaZyncException, self).__init__(msg, *args, **kwargs)

class SubmitWindow(object):
  """
  A Maya UI window for submitting to Zync
  """
  def __init__(self, title='Zync Submit (version %s)' % __version__):
    """
    Constructs the window.
    You must call show() to display the window.
    """
    self.title = title

    scene_name = cmds.file(q=True, loc=True)
    if scene_name == 'unknown':
      err_msg = 'Please save your script before launching a job.'
      cmds.confirmDialog(title='Unsaved script',
        message=err_msg,
        button='OK', defaultButton='OK', icon='critical')
      cmds.error(err_msg)

    self.new_project_name = zync_conn.get_project_name(scene_name)

    self.num_instances = 1
    self.priority = 50
    self.parent_id = None

    self.project = proj_dir()
    if self.project[-1] == '/':
      self.project = self.project[:-1]

    self.frange = frame_range()
    self.udim_range = udim_range()
    self.frame_step = cmds.getAttr('defaultRenderGlobals.byFrameStep')
    self.chunk_size = 10
    self.upload_only = 0
    self.start_new_slots = 1
    self.skip_check = 0
    self.notify_complete = 0
    self.vray_nightly = 0
    self.use_standalone = 0
    self.distributed = 0
    self.ignore_plugin_errors = 0
    self.login_type = 'zync'
    self.chunk_size_allowed = True

    mi_setting = zync_conn.CONFIG.get('USE_MI')
    if mi_setting in (None, '', 1, '1'):
      self.force_mi = True
    else:
      self.force_mi = False

    self.x_res = cmds.getAttr('defaultResolution.width')
    self.y_res = cmds.getAttr('defaultResolution.height')

    self.init_layers()
    self.init_bake()

    self.name = self.loadUI(UI_FILE)

    self.check_references()

  def loadUI(self, ui_file):
    """
    Loads the UI and does post-load commands.
    """
    # Maya 2016 and up will use Maya IO by default.
    self.is_maya_io = (int(get_maya_version()) >= 2016)
    # Create some new functions. These functions are called by UI elements in
    # resources/submit_dialog.ui. Each UI element in that file uses these
    # functions to query this window Object for its initial value.
    #
    # For example, the "frange" textbox calls cmds.submit_callb('frange'),
    # which causes its value to be set to whatever the value of self.frange
    # is currently set to.
    #
    # Initial values can also be function based. For example, the "renderer"
    # dropdown calls cmds.submit_callb('renderer'), which in turn triggers
    # self.init_renderer().
    #
    # The UI doesn't have a reference to this window Object, but it does have
    # access to the Maya API. So we monkey patch these new functions into the
    # API so the UI can in effect call class functions.
    cmds.submit_callb = partial(self.get_initial_value, self)
    cmds.do_submit_callb = partial(self.submit, self)
    cmds.login_with_google_callb = partial(self.login_with_google, self)
    cmds.logout_callb = partial(self.logout, self)

    #
    #  Delete the "SubmitDialog" window if it exists.
    #
    if cmds.window('SubmitDialog', q=True, ex=True):
      cmds.deleteUI('SubmitDialog')

    #
    #  Load the UI file. See the init_* functions below for more info on
    #  what each UI element does as it's loaded.
    #
    name = cmds.loadUI(f=ui_file)

    cmds.window(name, e=True, title=self.title)

    #
    #  Callbacks - set up functions to be called as UI elements are modified.
    #
    cmds.textField('num_instances', e=True, changeCommand=self.change_num_instances)
    cmds.optionMenu('instance_type', e=True, changeCommand=self.change_instance_type)
    cmds.radioButton('existing_project', e=True, onCommand=self.select_existing_project)
    cmds.radioButton('new_project', e=True, onCommand=self.select_new_project)
    cmds.checkBox('upload_only', e=True, changeCommand=self.upload_only_toggle)
    cmds.optionMenu('renderer', e=True, changeCommand=self.change_renderer)
    cmds.optionMenu('job_type', e=True, changeCommand=self.change_job_type)
    cmds.checkBox('distributed', e=True, changeCommand=self.distributed_toggle)
    cmds.textScrollList('layers', e=True, selectCommand=self.change_layers)
    # No point in even showing the standalone option to users of old Maya, where
    # we force standalone use.
    cmds.checkBox('use_standalone', e=True, changeCommand=self.change_standalone,
                  vis=self.is_maya_io)
    #
    #  Call a few of those callbacks now to set initial UI state.
    #
    self.change_renderer(self.renderer)
    self.select_new_project(True)

    return name

  def upload_only_toggle(self, checked):
    if checked:
      cmds.textField('num_instances', e=True, en=False)
      cmds.optionMenu('instance_type', e=True, en=False)
      cmds.checkBox('skip_check', e=True, en=False)
      cmds.checkBox('distributed', e=True, en=False)
      cmds.textField('output_dir', e=True, en=False)
      cmds.optionMenu('renderer', e=True, en=False)
      cmds.optionMenu('job_type', e=True, en=False)
      cmds.checkBox('vray_nightly', e=True, en=False)
      cmds.checkBox('use_standalone', e=True, en=False)
      cmds.textField('frange', e=True, en=False)
      cmds.textField('frame_step', e=True, en=False)
      cmds.textField('chunk_size', e=True, en=False)
      cmds.optionMenu('camera', e=True, en=False)
      cmds.textScrollList('layers', e=True, en=False)
      cmds.textField('x_res', e=True, en=False)
      cmds.textField('y_res', e=True, en=False)
    else:
      cmds.textField('num_instances', e=True, en=True)
      cmds.optionMenu('instance_type', e=True, en=True)
      cmds.checkBox('skip_check', e=True, en=True)
      cmds.textField('output_dir', e=True, en=True)
      cmds.optionMenu('renderer', e=True, en=True)
      if eval_ui('renderer', type='optionMenu', v=True) in ('vray', 'V-Ray'):
        cmds.checkBox('vray_nightly', e=True, en=True)
        cmds.checkBox('use_standalone', e=True, en=True)
        cmds.checkBox('distributed', e=True, en=True)
      else:
        cmds.checkBox('vray_nightly', e=True, en=False)
        cmds.checkBox('distributed', e=True, en=False)
      cmds.checkBox('use_standalone', e=True, en=False)
      cmds.optionMenu('job_type', e=True, en=True)
      cmds.textField('frange', e=True, en=True)
      cmds.textField('frame_step', e=True, en=True)
      cmds.textField('chunk_size', e=True, en=True)
      cmds.optionMenu('camera', e=True, en=True)
      cmds.textScrollList('layers', e=True, en=True)
      cmds.textField('x_res', e=True, en=True)
      cmds.textField('y_res', e=True, en=True)

  def distributed_toggle(self, checked):
    """Event triggered when the Distributed Rendering control
    is toggled.

    Args:
      checked: bool, whether the checkbox is checked
    """
    # if DR is on use of standalone is required
    cmds.checkBox('use_standalone', e=True, en=not checked, value=checked)

  def change_num_instances(self, *args, **kwargs):
    self.update_est_cost()

  def change_instance_type(self, *args, **kwargs):
    self.update_est_cost()

  def change_renderer(self, renderer):
    if renderer in ('vray', 'V-Ray'):
      renderer_key = 'vray'
      cmds.checkBox('vray_nightly', e=True, en=True)
      cmds.checkBox('distributed', e=True, en=True)
      cmds.checkBox('use_standalone', e=True, en=True)
      cmds.checkBox('use_standalone', e=True, v=False)
      cmds.checkBox('use_standalone', e=True, label='Use Vray Standalone')
      self.chunk_size_allowed = True
      self._enable_resolution()
    elif renderer.lower() == 'arnold':
      renderer_key = 'arnold'
      cmds.checkBox('vray_nightly', e=True, en=False)
      cmds.checkBox('distributed', e=True, en=False)
      cmds.checkBox('use_standalone', e=True, en=True)
      cmds.checkBox('use_standalone', e=True, v=False)
      cmds.checkBox('use_standalone', e=True, label='Use Arnold Standalone')
      self.chunk_size_allowed = True
      self._enable_resolution()
    elif renderer.lower() == 'renderman':
      renderer_key = 'renderman'
      cmds.checkBox('vray_nightly', e=True, en=False)
      cmds.checkBox('distributed', e=True, en=False)
      cmds.checkBox('use_standalone', e=True, v=False)
      cmds.checkBox('use_standalone', e=True, en=False)
      cmds.checkBox('use_standalone', e=True, label='Use Standalone')
      self.chunk_size_allowed = False
      self._disable_resolution()
    else:
      raise MayaZyncException('Unrecognized renderer "%s".' % renderer)
    cmds.checkBox('vray_nightly', e=True, v=False)
    cmds.checkBox('distributed', e=True, v=False)
    cmds.textField('chunk_size', e=True, en=self.chunk_size_allowed)
    cmds.textField('chunk_size', e=True, tx=('10' if self.chunk_size_allowed else '1'))
    #
    #  job_types dropdown - remove all items for list, then allow in job types
    #  from zync_conn.JOB_SUBTYPES
    #
    old_types = cmds.optionMenu('job_type', q=True, ill=True)
    if old_types != None:
      cmds.deleteUI(old_types)
    first_type = None
    visible = False
    if renderer_key != None and renderer_key in self.job_types:
      for job_type in self.job_types[renderer_key]:
        if first_type == None:
          first_type = job_type
        label = string.capwords(job_type)
        if label != 'Render':
          visible = True
        print cmds.menuItem(parent='job_type', label=label)
    else:
      print cmds.menuItem(parent='job_type', label='Render')
      first_type = 'Render'
    cmds.optionMenu('job_type', e=True, vis=visible)
    cmds.text('job_type_label', e=True, vis=visible)
    self.change_job_type(first_type)
    # force refresh of a few other UI elements
    self.init_instance_type()
    self.update_est_cost()
    self.change_standalone(eval_ui('use_standalone', 'checkBox', v=True))
    self.init_output_dir()

  def _enable_resolution(self):
    cmds.textField('x_res', e=True, en=True)
    cmds.textField('y_res', e=True, en=True)
    msg = 'Resolution of rendered frames.'
    cmds.textField('x_res', e=True, annotation=msg)
    cmds.textField('y_res', e=True, annotation=msg)

  def _disable_resolution(self):
    cmds.textField('x_res', e=True, en=False)
    cmds.textField('y_res', e=True, en=False)
    msg = ('This renderer doesn\'t support changing render resolution.\n'
           'To change your resolution please change your scene render\n'
           'settings and save a new version of your scene.')
    cmds.textField('x_res', e=True, annotation=msg)
    cmds.textField('y_res', e=True, annotation=msg)

  def change_job_type(self, job_type):
    job_type = job_type.lower()
    if job_type == 'render':
      cmds.textField('output_dir', e=True, en=True)
      cmds.text('frange_label', e=True, label='Frame Range:')
      cmds.textField('frange', e=True, tx=self.frange)
      cmds.optionMenu('camera', e=True, en=True)
      cmds.text('layers_label', e=True, label='Render Layers:')
      cmds.textScrollList('layers', e=True, removeAll=True)
      cmds.textScrollList('layers', e=True, append=self.layers)
      cmds.textField('x_res', e=True, tx=self.x_res)
      cmds.textField('y_res', e=True, tx=self.y_res)
    elif job_type == 'bake':
      cmds.textField('output_dir', e=True, en=False)
      cmds.text('frange_label', e=True, label='UDIM Range:')
      cmds.textField('frange', e=True, tx=self.udim_range)
      cmds.optionMenu('camera', e=True, en=False)
      cmds.text('layers_label', e=True, label='Bake Sets:')
      cmds.textScrollList('layers', e=True, removeAll=True)
      cmds.textScrollList('layers', e=True, append=self.bake_sets)
      try:
        default_x_res = str(cmds.getAttr('vrayDefaultBakeOptions.resolutionX'))
      except:
        default_x_res = ''
      cmds.textField('x_res', e=True, tx=default_x_res)
      try:
        default_y_res = str(cmds.getAttr('vrayDefaultBakeOptions.resolutionY'))
      except:
        default_y_res = ''
      cmds.textField('y_res', e=True, tx=default_y_res)
    else:
      cmds.error('Unknown Job Type "%s".' % (job_type,))

  def change_layers(self):
    if cmds.optionMenu('job_type', q=True, v=True).lower() != 'bake':
      return
    if cmds.textScrollList('layers', q=True, nsi=True) > 1:
      return
    bake_sets = eval_ui('layers', 'textScrollList', ai=True, si=True)
    bake_set = bake_sets[0]
    cmds.textField('x_res', e=True, tx=cmds.getAttr('%s.resolutionX' % (bake_set,)))
    cmds.textField('y_res', e=True, tx=cmds.getAttr('%s.resolutionY' % (bake_set,)))

  def change_standalone(self, checked):
    """Event triggered when the Use Standalone control is toggled.

    Args:
      checked: bool, whether the checkbox is checked
    """
    current_renderer = eval_ui('renderer', type='optionMenu', v=True).lower()
    # if using arnold standalone, disable chunk size. arnold stores info
    # one-frame-per-file so chunk size is not applicable.
    if current_renderer == 'arnold' and checked:
      cmds.textField('chunk_size', e=True, en=False)
    else:
      cmds.textField('chunk_size', e=True, en=self.chunk_size_allowed)

  def select_new_project(self, selected):
    if selected:
      cmds.textField('new_project_name', e=True, en=True)
      cmds.optionMenu('existing_project_name', e=True, en=False)

  def select_existing_project(self, selected):
    if selected:
      cmds.textField('new_project_name', e=True, en=False)
      cmds.optionMenu('existing_project_name', e=True, en=True)

  def check_references(self):
    """
    Run any checks to ensure all reference files are accurate. If not,
    raise an Exception to halt the submit process.

    This function currently does nothing. Before Maya Binary was supported
    it checked to ensure no .mb files were being used.
    """
    #for ref in cmds.file(q=True, r=True):
    #   if check_failed:
    #     raise Exception(msg)
    pass

  def get_bake_set_uvs(self, bake_set):
    conn_list = cmds.listConnections(bake_set)
    if conn_list == None or len(conn_list) == 0:
      return None
    return cmds.polyEvaluate(conn_list[0], b2=True)

  def get_bake_set_map(self, bake_set):
    return cmds.getAttr('%s.bakeChannel' % (bake_set,))

  def get_bake_set_shape(self, bake_set):
    transforms = cmds.listConnections(bake_set)
    if transforms == None or len(transforms) == 0:
      return None
    transform = transforms[0]
    shape_nodes = cmds.listRelatives(transform)
    if shape_nodes == None or len(shape_nodes) == 0:
      return None
    return shape_nodes[0]

  def get_bake_set_output_path(self, bake_set):
    out_path = cmds.getAttr('%s.outputTexturePath' % (bake_set,))
    out_path = out_path.replace('\\', '/')
    if out_path[0] == '/' or out_path[1] == ':':
      full_path = out_path
    else:
      full_path = proj_dir().replace('\\', '/')
      if full_path[-1] != '/':
        full_path += '/'
      full_path += out_path
    return full_path

  def get_render_params(self):
    """
    Returns a dict of all the render parameters set on the UI
    """
    params = dict()

    if cmds.radioButton('existing_project', q=True, sl=True) == True:
      proj_name = eval_ui('existing_project_name', 'optionMenu', v=True)
      if proj_name == None or proj_name.strip() == '':
        err_msg = 'Your project name cannot be blank. Please select New Project and enter a name.'
        cmds.confirmDialog(title='No project',
          message=err_msg,
          button='OK', defaultButton='OK', icon='critical')
        cmds.error(err_msg)
    else:
      proj_name = eval_ui('new_project_name', text=True)
    params['proj_name'] = proj_name

    parent = eval_ui('parent_id', text=True).strip()
    if parent != None and parent != '':
      params['parent_id'] = parent
    params['upload_only'] = int(eval_ui('upload_only', 'checkBox', v=True))
    params['start_new_slots'] = self.start_new_slots
    params['skip_check'] = int(eval_ui('skip_check', 'checkBox', v=True))
    params['notify_complete'] = self.notify_complete
    params['project'] = eval_ui('project', text=True)

    #
    # Get the output path. If it is a relative path, convert it to an
    # absolute path by joining it to the Maya project path.
    #
    params['out_path'] = eval_ui('output_dir', text=True)
    if not os.path.isabs(params['out_path']):
      params['out_path'] = os.path.abspath(os.path.join(params['project'],
        params['out_path']))

    params['ignore_plugin_errors'] = int(eval_ui('ignore_plugin_errors', 'checkBox', v=True))

    params['renderer'] = self.get_renderer()

    params['job_subtype'] = eval_ui('job_type', type='optionMenu', v=True).lower()

    params['priority'] = int(eval_ui('priority', text=True))
    params['num_instances'] = int(eval_ui('num_instances', text=True))

    selected_type = eval_ui('instance_type', 'optionMenu', v=True)
    for inst_type in zync_conn.INSTANCE_TYPES:
      if selected_type.split(' (')[0] == inst_type:
        params['instance_type'] = inst_type
        break

    params['frange'] = eval_ui('frange', text=True)
    params['step'] = int(eval_ui('frame_step', text=True))
    params['chunk_size'] = int(eval_ui('chunk_size', text=True))
    params['camera'] = eval_ui('camera', 'optionMenu', v=True)
    params['xres'] = int(eval_ui('x_res', text=True))
    params['yres'] = int(eval_ui('y_res', text=True))
    params['use_standalone'] = 0

    if params['upload_only'] == 0 and params['renderer'] == 'vray':
      params['vray_nightly'] = int(eval_ui('vray_nightly', 'checkBox', v=True))
      if params['use_standalone'] == 1 and params['job_subtype'] == 'bake':
        cmds.error('Vray Standalone is not currently supported for Bake jobs.')
      params['distributed'] = int(eval_ui('distributed', 'checkBox', v=True))
      if params['distributed'] == 1 and params['job_subtype'] == 'bake':
        cmds.error('Distributed Rendering is not currently supported for Bake jobs.')
    elif params['upload_only'] == 0 and params['renderer'] == 'mr':
      params['vray_nightly'] = 0
      params['distributed'] = 0
    elif params['upload_only'] == 0 and params['renderer'] == 'arnold':
      params['vray_nightly'] = 0
      params['distributed'] = 0
    else:
      params['vray_nightly'] = 0
      params['distributed'] = 0

    if params['upload_only'] == 1:
      params['layers'] = None
      params['bake_sets'] = None
    elif params['job_subtype'] == 'bake':
      bake_sets = eval_ui('layers', 'textScrollList', ai=True, si=True)
      if not bake_sets:
        msg = 'Please select bake set(s).'
        raise MayaZyncException(msg)
      bake_sets = ','.join(bake_sets)
      params['bake_sets'] = bake_sets
      params['layers'] = None
    else:
      layers = eval_ui('layers', 'textScrollList', ai=True, si=True)
      if not layers:
        msg = 'Please select layer(s) to render.'
        raise MayaZyncException(msg)
      layers = ','.join(layers)
      params['layers'] = layers
      params['bake_sets'] = None

    return params

  def show(self):
    """
    Displays the window.
    """
    cmds.showWindow(self.name)

  def init_bake(self):
    self.bake_sets = (bake_set for bake_set in cmds.ls(type='VRayBakeOptions') \
      if bake_set != 'vrayDefaultBakeOptions')
    self.bake_sets = list(self.bake_sets)
    self.bake_sets.sort()

  #
  #  These init_* functions get run automatcially when the UI file is loaded.
  #  The function names must match the name of the UI element e.g. init_camera()
  #  will be run when the "camera" UI element is initialized.
  #

  def init_layers(self):
    self.layers = []
    try:
      all_layers = cmds.ls(type='renderLayer', showNamespace=True)
      for i in range(0, len(all_layers), 2):
        if all_layers[i+1] == ':':
          self.layers.append(all_layers[i])
    except Exception:
      self.layers = cmds.ls(type='renderLayer')

  def init_existing_project_name(self):
    self.projects = zync_conn.get_project_list()
    project_found = False
    for project in self.projects:
      cmds.menuItem(parent='existing_project_name', label=project['name'])
      if project['name'] == self.new_project_name:
        project_found = True
    if project_found:
      cmds.optionMenu('existing_project_name', e=True, v=self.new_project_name)
    if len(self.projects) == 0:
      cmds.radioButton('existing_project', e=True, en=False)
    else:
      cmds.radioButton('existing_project', e=True, en=True)

  def init_instance_type(self):
    current_selected = eval_ui('instance_type', type='optionMenu', v=True)
    if current_selected == None:
      current_machine_type = None
    else:
      current_machine_type = current_selected.split(' (')[0]
    old_types = cmds.optionMenu('instance_type', q=True, ill=True)
    if old_types != None:
      cmds.deleteUI(old_types)
    current_renderer = None
    menu_option = eval_ui('renderer', type='optionMenu', v=True)
    current_renderer = self.get_renderer()
    sorted_types = [t for t in zync_conn.INSTANCE_TYPES]
    sorted_types.sort(zync_conn.compare_instance_types)
    set_to = None
    for inst_type in sorted_types:
      label = '%s (%s)' % (inst_type, zync_conn.INSTANCE_TYPES[inst_type]['description'].replace(', preemptible',''))
      if current_renderer != None:
        inst_type_base = inst_type.split(' ')[-1]
        field_name = 'CP-ZYNC-%s-%s' % (inst_type_base.upper(), current_renderer.upper())
        if 'PREEMPTIBLE' in inst_type.upper():
          field_name += '-PREEMPTIBLE'
        if (field_name in zync_conn.PRICING['gcp_price_list'] and
          'us' in zync_conn.PRICING['gcp_price_list'][field_name]):
          cost = '$%.02f' % (float(zync_conn.PRICING['gcp_price_list'][field_name]['us']),)
          label += ' %s' % (cost,)
      if inst_type == current_machine_type:
        set_to = label
      cmds.menuItem(parent='instance_type', label=label)
    if set_to != None:
      cmds.optionMenu('instance_type', e=True, v=set_to)
    self.update_est_cost()

  def init_renderer(self):
    #
    #  Try to detect the currently selected renderer, so it will be selected
    #  when the form appears. If we can't, fall back to the default set in zync.py.
    #
    current_renderer = cmds.getAttr('defaultRenderGlobals.currentRenderer')
    if current_renderer == 'mentalRay':
      key = 'mr'
    elif current_renderer == 'vray':
      key = 'vray'
    elif current_renderer == 'arnold':
      key = 'arnold'
    # handle 'renderMan' and 'renderManRIS'
    elif current_renderer.startswith('renderMan'):
      key = 'renderman'
    else:
      key = 'vray'
    # if that renderer is not supported, default to Vray
    default_renderer_name = zync_conn.MAYA_RENDERERS.get(key, 'vray')
    self.renderer = key
    #
    #  Add the list of renderers to UI element.
    #
    rend_found = False
    for item in zync_conn.MAYA_RENDERERS.values():
      cmds.menuItem(parent='renderer', label=item)
      if item == default_renderer_name:
        rend_found = True
    if rend_found:
      cmds.optionMenu('renderer', e=True, v=default_renderer_name)

  def init_job_type(self):
    self.job_types = zync_conn.JOB_SUBTYPES['maya']

  def init_camera(self):
    cam_parents = [cmds.listRelatives(x, ap=True)[-1] for x in cmds.ls(cameras=True)]
    for cam in cam_parents:
      if (cmds.getAttr(cam + '.renderable')) == True:
        cmds.menuItem(parent='camera', label=cam)

  def init_output_dir(self):
    # renderman doesn't use standard project settings, it has its own
    # preference.
    if self.get_renderer() == 'renderman':
      default_output_dir = maya.mel.eval('rmanGetDir rfmImages')
    else:
      # the project settings define where that project's rendered images should
      # go. get this project setting, defaulting to "images" if it's not found
      # or blank.
      images_rule = cmds.workspace(fileRuleEntry='images')
      if not images_rule or not images_rule.strip():
        images_rule = 'images'
      # this is usually a relative path, and if it is it's relative to the
      # project directory. if image_rule is an absolute path os.path.join
      # will throw out the project dir.
      default_output_dir = os.path.join(cmds.workspace(q=True, rd=True), images_rule)
    cmds.textField('output_dir', e=True, tx=default_output_dir)

  def update_est_cost(self):
    machine_type = eval_ui('instance_type', type='optionMenu', v=True)
    if machine_type != None:
      machine_type = machine_type.split(' (')[0]
      renderer_label = eval_ui('renderer', type='optionMenu', v=True)
      renderer = self.get_renderer()
      if renderer != None:
        num_machines = int(eval_ui('num_instances', text=True))
        machine_type_base = machine_type.split(' ')[-1]
        field_name = 'CP-ZYNC-%s-%s' % (machine_type_base.upper(), renderer.upper())
        if 'PREEMPTIBLE' in machine_type.upper():
          field_name += '-PREEMPTIBLE'
        if (field_name in zync_conn.PRICING['gcp_price_list'] and
          'us' in zync_conn.PRICING['gcp_price_list'][field_name]):
          text = '$%.02f' % ((num_machines * zync_conn.PRICING['gcp_price_list'][field_name]['us']),)
        else:
          text = 'Not Available'
      else:
        text = 'Not Available'
    else:
      text = 'Not Available'
    cmds.text('est_cost', e=True, label='Est. Cost per Hour: %s' % (text,))

  def get_renderer(self):
    """Get the renderer which is currently selected in the Zync plugin.
    The label shown in the menu (and returned be eval_ui) is slightly
    different than what we want, so we need to translate it based on
    the master list of renderers.

    Returns:
      str, the currently selected renderer, or None if we weren't
      able to identify the one selected.
    """
    selected_renderer_label = eval_ui('renderer', type='optionMenu', v=True)
    for renderer, renderer_label in zync_conn.MAYA_RENDERERS.iteritems():
      if renderer_label == selected_renderer_label:
        return renderer
    return None

  def get_scene_info(self, renderer):
    """
    Returns scene info for the current scene.
    """

    print '--> initializing'
    clear_layer_info()

    print '--> render layers'
    scene_info = {'render_layers': self.layers}

    print '--> checking selections'
    if eval_ui('job_type', type='optionMenu', v=True).lower() == 'bake':
      selected_bake_sets = eval_ui('layers', 'textScrollList', ai=True, si=True)
      if selected_bake_sets == None:
        selected_bake_sets = []
      selected_layers = []
    else:
      selected_layers = eval_ui('layers', 'textScrollList', ai=True, si=True)
      if selected_layers == None:
        selected_layers = []
      selected_bake_sets = []

    #
    #  Detect a list of referenced files. We must use ls() instead of file(q=True, r=True)
    #  because the latter will only detect references one level down, not nested references.
    #
    print '--> references'
    scene_info['references'] = []
    scene_info['unresolved_references'] = []
    for ref_node in cmds.ls(type='reference'):
      try:
        scene_info['references'].append(cmds.referenceQuery(ref_node, filename=True))
        scene_info['unresolved_references'].append(
          cmds.referenceQuery(ref_node, filename=True, unresolvedName=True))
      except:
        pass

    print '--> render passes'
    scene_info['render_passes'] = {}
    if renderer == 'vray' and cmds.getAttr('vraySettings.imageFormatStr') != 'exr (multichannel)':
      pass_list = cmds.ls(type='VRayRenderElement')
      pass_list += cmds.ls(type='VRayRenderElementSet')
      if len(pass_list) > 0:
        for layer in selected_layers:
          scene_info['render_passes'][layer] = []
          enabled_passes = get_layer_override(layer, renderer, 'render_passes')
          for r_pass in pass_list:
            if r_pass in enabled_passes:
              vray_name = None
              vray_explicit_name = None
              vray_file_name = None
              for attr_name in cmds.listAttr(r_pass):
                if attr_name.startswith('vray_filename'):
                  vray_file_name = cmds.getAttr('%s.%s' % (r_pass, attr_name))
                elif attr_name.startswith('vray_name'):
                  vray_name = cmds.getAttr('%s.%s' % (r_pass, attr_name))
                elif attr_name.startswith('vray_explicit_name'):
                  vray_explicit_name = cmds.getAttr('%s.%s' % (r_pass, attr_name))
              if vray_file_name != None and vray_file_name != "":
                final_name = vray_file_name
              elif vray_explicit_name != None and vray_explicit_name != "":
                final_name = vray_explicit_name
              elif vray_name != None and vray_name != "":
                final_name = vray_name
              else:
                continue
              # special case for Material Select elements - these are named based on the material
              # they are connected to.
              if 'vray_mtl_mtlselect' in cmds.listAttr(r_pass):
                connections = cmds.listConnections('%s.vray_mtl_mtlselect' % (r_pass,))
                if connections:
                  final_name += '_%s' % (str(connections[0]),)
              scene_info['render_passes'][layer].append(final_name)

    print '--> bake sets'
    scene_info['bake_sets'] = {}
    for bake_set in selected_bake_sets:
      scene_info['bake_sets'][bake_set] = {
        'uvs': self.get_bake_set_uvs(bake_set),
        'map': self.get_bake_set_map(bake_set),
        'shape': self.get_bake_set_shape(bake_set),
        'output_path': self.get_bake_set_output_path(bake_set)
      }

    print '--> frame extension & padding'
    if renderer == 'vray':
      scene_info['extension'] = cmds.getAttr('vraySettings.imageFormatStr')
      if scene_info['extension'] == None:
        scene_info['extension'] = 'png'
      scene_info['padding'] = int(cmds.getAttr('vraySettings.fileNamePadding'))
    elif renderer == 'mr':
      scene_info['extension'] = cmds.getAttr('defaultRenderGlobals.imfPluginKey')
      if not scene_info['extension']:
        scene_info['extension'] = get_default_extension(renderer)
      scene_info['padding'] = int(cmds.getAttr('defaultRenderGlobals.extensionPadding'))
    elif renderer == 'arnold':
      scene_info['extension'] = cmds.getAttr('defaultRenderGlobals.imfPluginKey')
      scene_info['padding'] = int(cmds.getAttr('defaultRenderGlobals.extensionPadding'))
    elif renderer == 'renderman':
      if cmds.getAttr('defaultRenderGlobals.outFormatControl'):
        scene_info['extension'] = cmds.getAttr('defaultRenderGlobals.outFormatExt').lstrip('.')
      else:
        scene_info['extension'] = _rman_translate_format_to_extension(
            cmds.getAttr('rmanFinalOutputGlobals0.rman__riopt__Display_type'))
      scene_info['padding'] = int(cmds.getAttr('defaultRenderGlobals.extensionPadding'))
    scene_info['extension'] = scene_info['extension'][:3]

    # collect a dict of attrs that define how output frames have frame numbers
    # and extension added to their names.
    if renderer == 'arnold':
      print '--> output name format'
      scene_info['output_name_format'] = {}
      attr_list = {
        'outFormatControl',
        'animation',
        'putFrameBeforeExt',
        'periodInExt',
        'extensionPadding',
      }
      for name_attr in attr_list:
        if cmds.attributeQuery(name_attr, n='defaultRenderGlobals', ex=True):
          scene_info['output_name_format'][name_attr] = cmds.getAttr('defaultRenderGlobals.%s' % name_attr)

    print '--> output file prefixes'
    scene_info['file_prefix'] = [get_layer_override('defaultRenderLayer', renderer, 'prefix')]
    layer_prefixes = {}
    for layer in selected_layers:
      layer_prefix = get_layer_override(layer, renderer, 'prefix')
      if layer_prefix != None:
        layer_prefixes[layer] = layer_prefix
    scene_info['file_prefix'].append(layer_prefixes)

    print '--> files'
    scene_info['files'] = list(set(get_scene_files()))

    print '--> plugins'
    scene_info['plugins'] = []
    plugin_list = cmds.pluginInfo(query=True, pluginsInUse=True)
    for i in range(0, len(plugin_list), 2):
      scene_info['plugins'].append(str(plugin_list[i]))

    # detect MentalCore
    if renderer == 'mr':
      mentalcore_used = False
      try:
        mc_nodes = cmds.ls(type='core_globals')
        if len(mc_nodes) == 0:
          mentalcore_used = False
        else:
          mc_node = mc_nodes[0]
          if cmds.getAttr('%s.ec' % (mc_node,)) == True:
            mentalcore_used = True
          else:
            mentalcore_used = False
      except:
        mentalcore_used = False
    else:
      mentalcore_used = False
    if mentalcore_used:
      scene_info['plugins'].append('mentalcore')

    # detect use of cache files
    if len(cmds.ls(type='cacheFile')) > 0:
      scene_info['plugins'].append('cache')

    print '--> maya version'
    scene_info['version'] = get_maya_version()

    scene_info['vray_version'] = ''
    if renderer == 'vray':
      print '--> vray version'
      try:
        scene_info['vray_version'] = str(cmds.pluginInfo('vrayformaya', query=True, version=True))
      except Exception as e:
        print str(e)
        raise MayaZyncException('Could not detect Vray version. This is required to render Vray jobs. Do you have the Vray plugin loaded?')

    scene_info['arnold_version'] = ''
    if renderer == 'arnold':
      print '--> arnold version'
      try:
        scene_info['arnold_version'] = str(cmds.pluginInfo('mtoa', query=True, version=True))
      except Exception as e:
        print str(e)
        raise MayaZyncException('Could not detect Arnold version. This is required to render Arnold jobs. Do you have the Arnold plugin loaded?')

    if renderer == 'renderman':
      print '--> renderman version'
      try:
        # Zync needs the prman version, not the RfM plugin version. until recently
        # these were not synchronized. prman version comes back like "prman 20.7 @1571626"
        scene_info['renderman_version'] = str(maya.mel.eval('rman getversion prman').split()[1])
      except Exception as e:
        print str(e)
        raise MayaZyncException('Could not detect Renderman version. This is required to render Renderman jobs. Do you have the Renderman plugin loaded?')

    # If this is an Arnold job and AOVs are on, include a list of AOV
    # names in scene_info. If "Merge AOVs" is on, i.e. multichannel EXRs,
    # the AOVs will be rendered in a single image, so consider AOVs to be
    # OFF for purposes of the Zync job.
    if renderer == 'arnold':
      try:
        aov_on = (cmds.getAttr('defaultArnoldRenderOptions.aovMode') and
          not cmds.getAttr('defaultArnoldDriver.mergeAOVs'))
      except:
        aov_on = False
      if aov_on:
        print '--> AOVs'
        scene_info['aovs'] = [cmds.getAttr('%s.name' % (n,)) for n in cmds.ls(type='aiAOV')]
      else:
        scene_info['aovs'] = []

    return scene_info

  @staticmethod
  def get_initial_value(window, name):
    """Returns the initial value for a given attribute.

    Args:
      window: The Zync Maya UI window
      name: str the attribute name

    Returns:
      str, the initial attribute value, or "Undefined" if the attribute was
        not found
    """
    init_name = '_'.join(('init', name))
    if hasattr(window, init_name):
      return getattr(window, init_name)()
    elif hasattr(window, name):
      return getattr(window, name)
    else:
      return 'Undefined'

  @staticmethod
  def login_with_google(window):
    """Perform the Google OAuth flow.

    Args:
      window: The Zync Maya UI window
    """
    window.login_type = 'google'
    user_email = zync_conn.login_with_google()
    cmds.text('google_login_status', e=True, label='Logged in as %s' % user_email)

  @staticmethod
  def logout(window):
    zync_conn.logout()
    cmds.text('google_login_status', e=True, label='')

  @staticmethod
  def submit(window):
    """Submit a job to Zync.

    Args:
      window: The Zync Maya UI window
    """
    if not zync_conn.has_user_login():
      cmds.confirmDialog(title='Not Logged In',
        message='You must login before submitting a new job.',
        button='OK', defaultButton='OK', icon='critical')
      return

    print 'Collecting render parameters...'
    scene_path = cmds.file(q=True, loc=True)
    params = window.get_render_params()

    print 'Collecting scene info...'
    params['scene_info'] = window.get_scene_info(params['renderer'])
    params['plugin_version'] = __version__

    try:
      if (not window.is_maya_io or
          eval_ui('use_standalone', 'checkBox', v=True)):
        frange_split = params['frange'].split(',')
        sf = int(frange_split[0].split('-')[0])

        if params['upload_only'] == 1:
          layer_list = ['defaultRenderLayer']
          ef = sf
        else:
          layer_list = params['layers'].split(',')
          ef = int(frange_split[-1].split('-')[-1])

        if params['renderer'] == 'vray':
          print 'Vray job, collecting additional info...'

          vrscene_path = window.get_standalone_scene_path('vrscene')

          print 'Exporting .vrscene files...'
          for layer in layer_list:
            print 'Exporting layer %s...' % (layer,)
            possible_scene_names, layer_params = window.export_vrscene(
                vrscene_path, layer, params, sf, ef)

            layer_file = None
            for possible_scene_name in possible_scene_names:
              if os.path.exists(possible_scene_name):
                layer_file = possible_scene_name
                break
            if layer_file is None:
              raise zync.ZyncError('the .vrscene file generated by the Zync Maya plugin '
                                   'was not found. Unable to submit job.')

            print 'Submitting job for layer %s...' % (layer,)
            zync_conn.submit_job('vray', layer_file, params=layer_params)

        elif params['renderer'] == 'arnold':
          print 'Arnold job, collecting additional info...'

          ass_path = window.get_standalone_scene_path('ass')

          print 'Exporting .ass files...'
          for layer in layer_list:
            print 'Exporting layer %s...' % (layer,)
            layer_file_wildcard, layer_params = window.export_ass(ass_path,
                layer, params, sf, ef)
            print 'Submitting job for layer %s...' % (layer,)
            zync_conn.submit_job('arnold', layer_file_wildcard, params=layer_params)

        else:
          raise MayaZyncException('Renderer %s unsupported for standalone rendering.' % params['renderer'])

        cmds.confirmDialog(title='Success',
          message='{num_jobs} {label} submitted to Zync.'.format(
            num_jobs=len(layer_list),
            label='job' if len(layer_list) == 1 else 'jobs'),
          button='OK', defaultButton='OK')

      else:
        # Uncomment this section if you want to
        # save a unique copy of the scene file each time your submit a job.
        '''
        original_path = cmds.file(q=True, loc=True)
        original_modified = cmds.file(q=True, modified=True)
        scene_path = generate_scene_path()
        cmds.file(rename=scene_path)
        cmds.file(save=True, type='mayaAscii')
        cmds.file(rename=original_path)
        cmds.file(modified=original_modified)
        '''

        if not window.verify_eula_acceptance(zync_conn):
          cmds.error('Job submission canceled.')

        zync_conn.submit_job('maya', scene_path, params=params)
        cmds.confirmDialog(title='Success', message='Job submitted to Zync.',
          button='OK', defaultButton='OK')

    except zync.ZyncPreflightError as e:
      cmds.confirmDialog(title='Preflight Check Failed', message=str(e),
        button='OK', defaultButton='OK')

    except zync.ZyncError as e:
      cmds.confirmDialog(title='Submission Error',
        message='Error submitting job: %s' % (str(e),),
        button='OK', defaultButton='OK', icon='critical')

    else:
      print 'Done.'

  @staticmethod
  def export_vrscene(vrscene_path, layer, render_params, start_frame, end_frame):
    """Export a .vrscene of the current scene.

    Args:
      vrscene_path: str, path to which to export the .vrscene. A layer name will
                    be inserted into the filename.
      layer: str, the name of the render layer to export
      render_params: dict, render job parameters
      start_frame: int, the first frame to export
      end_frame: int, the last frame to export

    Returns:
      tuple:
        - list of possible locations where the .vrscene may be found (Vray adds
          layer names automatically and is sometimes inconsistent)
        - dict of render job parameters, with any modifications to make the
          job run similarly with Vray standalone.
    """
    cmds.undoInfo(openChunk=True)

    cmds.editRenderLayerGlobals(currentRenderLayer=layer)

    scene_path = cmds.file(q=True, loc=True)
    scene_head, extension = os.path.splitext(scene_path)
    scene_name = os.path.basename(scene_head)

    layer_params = copy.deepcopy(render_params)

    layer_params['project_dir'] = render_params['project']
    layer_params['output_dir'] = render_params['out_path']
    layer_params['use_nightly'] = render_params['vray_nightly']
    if ('extension' not in render_params['scene_info'] or
      render_params['scene_info']['extension'] == None or
      render_params['scene_info']['extension'].strip() == ''):
      layer_params['scene_info']['extension'] = 'png'

    tail = cmds.getAttr('vraySettings.fileNamePrefix')
    if not tail:
      tail = scene_name
    else:
      tail = tail.replace('%s', scene_name)
      tail = re.sub('<scene>', scene_name, tail, flags=re.IGNORECASE)
      clean_camera = layer_params['camera'].replace(':', '_')
      tail = re.sub('%l|<layer>|<renderlayer>', layer, tail,
        flags=re.IGNORECASE)
      tail = re.sub('%c|<camera>', clean_camera, tail, flags=re.IGNORECASE)
    if tail[-1] != '.':
      tail += '.'

    layer_params['output_filename'] = '%s.%s' % (
      tail, layer_params['scene_info']['extension'])
    layer_params['output_filename'] = layer_params['output_filename'].replace('\\', '/')

    # Set up render globals for vray export. These changes will
    # be reverted later when we run cmds.undo().
    #
    # Turn "Don't save image" OFF - this will ensure Vray knows to translate
    # all render output settings.
    cmds.setAttr('vraySettings.dontSaveImage', 0)
    # Turn rendering off.
    cmds.setAttr('vraySettings.vrscene_render_on', 0)
    # Turn Vrscene export on.
    cmds.setAttr('vraySettings.vrscene_on', 1)
    # Set the Vrscene export filename.
    cmds.setAttr('vraySettings.vrscene_filename', vrscene_path, type='string')
    # Ensure we export only a single file.
    cmds.setAttr('vraySettings.misc_separateFiles', 0)
    cmds.setAttr('vraySettings.misc_eachFrameInFile', 0)

    # Turn off Geom Cache. If you render a frame locally with this on, and then
    # immediately export to zync, the cached geometry is written to the file.
    # Any geo that has deformations are only rendered in the cached state and
    # not updated per frame. This is an issue with Vray and using 'vrend' instead
    # of BatchRender to export the vrscene.
    cmds.setAttr('vraySettings.globopt_cache_geom_plugins', 0)
    cmds.setAttr('vraySettings.globopt_cache_bitmaps', 0)

    # Set compression options.
    cmds.setAttr('vraySettings.misc_meshAsHex', 1)
    cmds.setAttr('vraySettings.misc_transformAsHex', 1)
    cmds.setAttr('vraySettings.misc_compressedVrscene', 1)
    # Turn the VFB off, make sure the viewer is hidden.
    cmds.setAttr('vraySettings.vfbOn', 0)
    cmds.setAttr('vraySettings.hideRVOn', 1)
    # Ensure animation is fully enabled and configured with the correct
    # frame range. This is usually the case already, but some users will
    # have it disabled expecting their existing local farm to update
    # with the correct settings.
    cmds.setAttr('vraySettings.animBatchOnly', 0)
    cmds.setAttr('defaultRenderGlobals.animation', 1)
    cmds.setAttr('defaultRenderGlobals.startFrame', start_frame)
    cmds.setAttr('defaultRenderGlobals.endFrame', end_frame)
    # Set resolution of the scene to layer resolution to avoid problems with regions.
    cmds.setAttr('vraySettings.width', layer_params['xres'])
    cmds.setAttr('vraySettings.height', layer_params['yres'])

    # Run the export.
    maya.mel.eval('vrend -camera "%s" -layer "%s"' % (layer_params['camera'], layer))

    cmds.undoInfo(closeChunk=True)
    cmds.undo()

    vrscene_base, ext = os.path.splitext(vrscene_path)
    if layer == 'defaultRenderLayer':
      possible_scene_names = [
        '%s_masterLayer%s' % (vrscene_base, ext),
        '%s%s' % (vrscene_base, ext),
        '%s_defaultRenderLayer%s' % (vrscene_base, ext)
      ]
    else:
      possible_scene_names = [
        '%s_%s%s' % (vrscene_base, layer, ext)
      ]

    return possible_scene_names, layer_params

  @staticmethod
  def export_ass(ass_path, layer, render_params, start_frame, end_frame):
    """Export .ass files of the current scene.

    Args:
      ass_path: str, path to which to export the .ass files
      layer: str, the name of the render layer to export
      render_params: dict, render job parameters
      start_frame: int, the first frame to export
      end_frame: int, the last frame to export

    Returns:
      tuple:
        - str path to the final export location. will contain a wildcard in
          place of frame number, to indicate the set of files produced.
        - dict of render job parameters, with any modifications to make the
          job run similarly with Arnold standalone.
    """
    cmds.undoInfo(openChunk=True)

    cmds.editRenderLayerGlobals(currentRenderLayer=layer)

    scene_path = cmds.file(q=True, loc=True)
    scene_head, extension = os.path.splitext(scene_path)
    scene_name = os.path.basename(scene_head)

    layer_params = copy.deepcopy(render_params)

    layer_params['project_dir'] = render_params['project']
    layer_params['output_dir'] = render_params['out_path']

    tail = cmds.getAttr('defaultRenderGlobals.imageFilePrefix')
    if not tail:
      tail = scene_name
    else:
      tail = tail.replace('%s', scene_name)
      tail = re.sub('<scene>', scene_name, tail, flags=re.IGNORECASE)
      clean_camera = render_params['camera'].replace(':', '_')
      tail = re.sub('%l|<layer>|<renderlayer>', layer, tail,
        flags=re.IGNORECASE)
      tail = re.sub('%c|<camera>', clean_camera, tail, flags=re.IGNORECASE)
      try:
        render_version = cmds.getAttr('defaultRenderGlobals.renderVersion')
        if render_version != None:
          tail = re.sub('%v|<version>',
            cmds.getAttr('defaultRenderGlobals.renderVersion'),
            tail, flags=re.IGNORECASE)
      except ValueError:
        pass
    if tail[-1] != '.':
      tail += '.'

    layer_params['output_filename'] = '%s.%s' % (
      tail, render_params['scene_info']['extension'])
    layer_params['output_filename'] = layer_params['output_filename'].replace('\\', '/')

    ass_base, ext = os.path.splitext(ass_path)
    layer_file = '%s_%s%s' % (ass_base, layer, ext)
    layer_file_wildcard = '%s_%s*%s' % (ass_base, layer, ext)

    ass_cmd = ('arnoldExportAss -f "%s" -endFrame %s -mask 255 ' % (layer_file, end_frame) +
      '-lightLinks 1 -frameStep %d.0 -startFrame %s ' % (layer_params['step'], start_frame) +
      '-shadowLinks 1 -cam %s' % (render_params['camera'],))
    maya.mel.eval(ass_cmd)

    cmds.undoInfo(closeChunk=True)
    cmds.undo()

    return layer_file_wildcard, layer_params

  @staticmethod
  def get_standalone_scene_path(suffix):
    """Get a file path for exporting a standalone scene, based on current scene
    and matching the Zync convention of where these files should be stored.

    This does NOT perform the actual export, only returns the path at which
    it should be stored.

    Args:
      suffix: str, the suffix of the filename e.g. "vrscene" or "ass"

    Returns:
      str the standalone scene file path
    """
    scene_path = cmds.file(q=True, loc=True)
    scene_head, _ = os.path.splitext(scene_path)
    scene_name = os.path.basename(scene_head)
    return zync_conn.generate_file_path(
        '%s.%s' % (scene_head, suffix)).replace('\\', '/')

  @staticmethod
  def verify_eula_acceptance(zync_conn):
    """Verify Autodesk EULA acceptance and if needed perform acceptance flow.

    Args:
      zync_conn: zync.Zync, connection to Zync

    Returns:
      bool, True if EULA is accepted, False if user declined
    """
    # find the Maya EULA
    maya_eula = None
    for eula in zync_conn.get_eulas():
      if eula.get('eula_kind').lower() == 'mayaio':
        maya_eula = eula
        break
    # blank accepted_by field indicates not yet accepted
    if maya_eula and not maya_eula.get('accepted_by'):
      eula_url = '%s/account#legal' % zync_conn.url
      # let the user know what's about to happen
      cmds.confirmDialog(title='Accept EULA', message=('In order to launch ' +
                         'Maya jobs you must accept the Autodesk EULA. It ' +
                         'looks like you haven\'t accepted this yet.\n\nA ' +
                         'browser window will open so you can do this, then ' +
                         'you\'ll be able to submit your job.\n\nURL: ' +
                         eula_url), button=['OK'], defaultButton='OK')
      # open page in browser
      webbrowser.open(eula_url)
      # wait for user to let us know they've responded
      eula_response = cmds.confirmDialog(title='Accept EULA', message=('Have ' +
          'you accepted the EULA?'), button=['Yes', 'No'], defaultButton='Yes',
          cancelButton='No', dismissString='No')

      if eula_response == 'No':
        return False

    return True

def submit_dialog():
  submit_window = SubmitWindow()
  submit_window.show()
