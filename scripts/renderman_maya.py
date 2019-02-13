"""
Zync Maya Plugin - Renderman
"""

import os
import re
from Queue import Queue

import maya.cmds as cmds
import maya.mel

import maya_common


class RendermanPre22Api(object):
  def get_version(self):
    return str(maya.mel.eval('rman getversion prman').split()[1])

  def get_extension(self):
    return self._translate_format_to_extension(
        cmds.getAttr('rmanFinalOutputGlobals0.rman__riopt__Display_type'))

  def _translate_format_to_extension(self, image_format):
    """Translate an image format to the extension of files it
    generates. For example, "openexr" becomes "exr".

    Args:
      image_format: str, the image format

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
      format_index = formats_list.index(image_format)
    except ValueError:
      return image_format
    return formats_list[format_index+1]

  def get_output_dir(self):
    return maya.mel.eval('rmanGetDir rfmImages')

  def expand_string(self, str):
    return maya.mel.eval('rman subst "%s"' % str)


class RendermanApi(object):
  def get_version(self):
    import rfm2
    return str(rfm2.config.cfg().build_info.version())

  def get_extension(self):
    import rfm2
    display_dict = rfm2.api.displays.get_displays()
    main_display_path = rfm2.api.strings.expand_string(display_dict['displays']['beauty']['filePath'])
    return os.path.splitext(main_display_path)[1][1:]

  def get_output_dir(self):
    import rfm2
    return rfm2.api.scene.get_image_dir()

  def expand_string(self, str):
    import rfm2
    return rfm2.api.strings.expand_string(str)


class Renderman(object):
  def __init__(self):
    self.camera = None
    self.layers_to_render = None
    self.renderman_api = None

  def init(self, layers_to_render, camera):
    self.layers_to_render = layers_to_render
    self.camera = camera

  def get_version(self):
    return self._get_api().get_version()

  def get_extension(self):
    return self._get_api().get_extension()

  def get_output_dir(self):
    return self._get_api().get_output_dir()

  def expand_string(self, str):
    return self._get_api().expand_string(str)

  def generate_files_from_tokenized_path(self, tokenizedPath):
    """Resolve all placeholders using Renderman function, but replace frame tags
       with wildcard, and resolve layers and camera."""
    expandedPath = tokenizedPath
    expandedPath = expandedPath.replace('<frame>', '*')
    expandedPath = expandedPath.replace('<f>', '*')
    expandedPath = expandedPath.replace('<f2>', '*')
    expandedPath = expandedPath.replace('<f3>', '*')
    expandedPath = expandedPath.replace('<f4>', '*')

    expandedPath = re.sub(maya_common._SUBSTITUTE_CAMERA_TOKEN_RE, self.camera, expandedPath)

    allPaths = []
    if re.match(maya_common._HAS_LAYER_TOKEN_RE, expandedPath):
      for layer in self.layers_to_render:
        allPaths.append(re.sub(maya_common._SUBSTITUTE_LAYER_TOKEN_RE, layer, expandedPath))
    else:
      allPaths.append(expandedPath)

    for path in allPaths:
      path = self.expand_string(path)
      import fnmatch
      for dirPath, dirs, files in os.walk(os.path.dirname(path)):
        for filename in fnmatch.filter(files, os.path.basename(path)):
          yield os.path.join(dirPath, filename)

  # Dependency detection
  def parse_rib_archives(self):
    queue = Queue()
    self._enqueue_rib_files(queue)
    if queue.empty():
      return

    for file in self._process_rib_queue(queue):
      yield file

    if cmds.progressWindow(query=1, isCancelled=1):
      cmds.progressWindow(endProgress=1)
      raise maya_common.MayaZyncException("Submission cancelled")

    cmds.progressWindow(endProgress=1)

  def _enqueue_rib_files(self, queue):
    nodes = cmds.ls(type='RenderManArchive')
    for node in nodes:
      for file in self.ribArchive_handler(node):
        queue.put((file, node, 'rib'))

  def _process_rib_queue(self, queue):
    files_parsed = 0
    cmds.progressWindow(title='Parsing rib files for dependencies...',
                        progress=files_parsed, maxValue=files_parsed + queue.qsize(),
                        status='Parsing: %d of %d' % (files_parsed, files_parsed + queue.qsize()), isInterruptable=True)

    while not queue.empty() and not cmds.progressWindow(query=1, isCancelled=1):
      (file, node, file_type) = queue.get()
      files_parsed += 1
      cmds.progressWindow(edit=True, progress=files_parsed, maxValue=files_parsed + queue.qsize(),
                          status='Parsing: %d of %d' % (files_parsed, files_parsed + queue.qsize()))

      scene_file = file.replace('\\', '/')
      print 'found file dependency from %s node %s: %s' % ('RenderManArchive', node, scene_file)
      yield scene_file

      if file_type == 'rib':
        for (f, t) in self._parse_rib_archive(file):
          queue.put((f, node, t))

  def _parse_rib_archive(self, ribArchivePath):
    """Parses RIB archive file and tries to extract texture file names and other .rib files to parse.
       It read the file line by line with buffer limit, because the files can be very big.
       RIB files can be binary, in which case parsing them would be possible, but hacky,
       so we won't do that.
       We also check if the user has cancelled."""

    fileSet = set()

    # Please see the link to easily see what those regex match: https://regex101.com/r/X1hBUJ/1
    patterns = [(r'\"((?:(?!\").)*?\.rib)\"', 'rib'),
                (r'\"string fileTextureName\" \[\"((?:(?!\").)*?)\"', 'tex'),
                (r'\"string lightColorMap\" \[\"((?:(?!\").)*?)\"', 'tex'),
                (r'\"string filename\" \[\"((?:(?!\").)*?)\"', 'tex')]
    with open(ribArchivePath, 'r') as content_file:
      line = content_file.readline(10000)
      while line != '' and not cmds.progressWindow(query=1, isCancelled=1):
        for (pattern, t) in patterns:
          for file in re.findall(pattern, line):
            if os.path.exists(file):
              fileSet.add((file, t))
        line = content_file.readline(10000)

    for (f, t) in fileSet:
      yield (f, t)

  def _get_api(self):
    if self.renderman_api is not None:
      return self.renderman_api
    try:
      import rfm2
      self.renderman_api = RendermanApi()
    except ImportError as e:
      self.renderman_api = RendermanPre22Api()
    return self.renderman_api

  # Node handlers
  def ribArchive_handler(self, node):
    """Handles RIB archive nodes"""
    archive_path = cmds.getAttr('%s.filename' % node)
    for ribArchivePath in self.generate_files_from_tokenized_path(archive_path):
      yield ribArchivePath

  def pxrStdEnvMap_handler(self, node):
    """Handles PxrStdEnvMapLight nodes, up to Renderman 20"""
    filename = cmds.getAttr('%s.rman__EnvMap' % node)
    for expandedPath in self.generate_files_from_tokenized_path(filename):
      yield expandedPath


  def pxrTexture_handler(self, node):
    """Handles PxrTexture nodes"""
    filename = cmds.getAttr('%s.filename' % node)
    if cmds.getAttr('%s.atlasStyle' % node) != 0:
      filename = re.sub('_MAPID_', '*', filename)
    for expandedPath in self.generate_files_from_tokenized_path(filename):
      yield expandedPath


  def pxrMultiTexture_handler(self, node):
    """Handles PxrMultiTexture nodes"""
    for texture_id in range(0,10):
      filename = cmds.getAttr('%s.filename%d' % (node, texture_id))
      if filename:
        for expandedPath in self.generate_files_from_tokenized_path(filename):
          yield expandedPath


  def pxrDomeLight_handler(self, node):
    """Handles PxrDomeLight nodes since Renderman 21"""
    filename = cmds.getAttr('%s.lightColorMap' % node)
    if filename:
      for expandedPath in self.generate_files_from_tokenized_path(filename):
          yield expandedPath


  def rmsEnvLight_handler(self, node):
    """Handles RMSEnvLight nodes"""
    filename = cmds.getAttr('%s.rman__EnvMap' % node)
    for expandedPath in self.generate_files_from_tokenized_path(filename):
      yield expandedPath


  def pxrPtexture_handler(self, node):
    filename = cmds.getAttr('%s.filename' % node)
    for expandedPath in self.generate_files_from_tokenized_path(filename):
      yield expandedPath


  def pxrNormalMap_handler(self, node):
    filename = cmds.getAttr('%s.filename' % node)
    for expandedPath in self.generate_files_from_tokenized_path(filename):
      yield expandedPath
