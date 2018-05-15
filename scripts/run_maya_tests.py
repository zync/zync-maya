#!/usr/bin/env python
"""Runs a suite of Zync Maya unit tests.

The Zync Maya unit tests must be run from within a Maya Python environment,
so rather than running tests directly this script wraps mayapy.

This script can run either the full set of sceneless tests, which do not
require a Maya scene to be loaded, or it can run the scene-based tests
on an individual Maya scene that is passed to it.

If you are running a scene test, you must also provide --info-file, a
JSON file containing the results you expect to get from get_scene_info in
the main plugin.
"""

import argparse
import ast
import logging
import os
import platform
import subprocess
import tempfile


def _get_maya_install_dir(maya_version):
  """Gets Maya install location."""
  # No Windows support here at the moment.
  if platform.system() == 'Darwin':
    return '/Applications/Autodesk/maya%s/Maya.app/Contents/bin' % maya_version
  else:
    # Prefer Maya I/O if it's installed.
    if os.path.isdir('/usr/autodesk/mayaIO%s' % maya_version):
      return '/usr/autodesk/mayaIO%s/bin' % maya_version
    return '/usr/autodesk/maya%s/bin' % maya_version


def _get_mayapy_path(maya_version):
  """Gets location of mayapy."""
  return os.path.join(_get_maya_install_dir(maya_version), 'mayapy')


def _get_maya_bin_path(maya_version):
  """Gets location of main Maya executable."""
  return os.path.join(_get_maya_install_dir(maya_version), 'maya')


def _get_scene_info_mel_script(renderer, layers, output_file):
  script_text = 'python("import zync_maya"); '
  script_text += 'string $scene_info = python("zync_maya.get_scene_info('
  script_text += '\'%s\', ' % renderer
  # List of layers being rendered comes in a comma-separated string, no need to join.
  script_text += '[\'%s\'], ' % layers
  script_text += 'False, [], [])"); '
  script_text += 'string $output_file = "%s"; ' % output_file
  script_text += '$fp = `fopen $output_file "w"`; '
  script_text += 'fprint $fp $scene_info; '
  script_text += 'fclose $fp; '
  return script_text


def _clean_unicode_from_object(input_obj):
  """Returns a version of an object with all unicode replaced by standard strings.

  json.loads returns an object containing unicode values, this method helps us clean that
  for easier comparison.

  Args:
    input_obj: Python object to be cleaned - dict, list, str, etc. This function will
        recurse into that object to convert all nested unicode values.
  """
  if isinstance(input_obj, dict):
    return {_clean_unicode_from_object(key): _clean_unicode_from_object(value)
            for key, value in input_obj.iteritems()}
  elif isinstance(input_obj, list):
    return [_clean_unicode_from_object(element) for element in input_obj]
  elif isinstance(input_obj, unicode):
    return input_obj.encode('utf-8')
  else:
    return input_obj


def run_maya_and_get_scene_info(scene, renderer, layers, maya_version):
  # Write out a temporary MEL script which wraps the call to zync-maya.
  # We could use mayapy instead but mayapy has proven unreliable in initializing
  # its environment in the same way as standard maya.
  with tempfile.NamedTemporaryFile() as mel_script:
    # Maya produces a lot of output on startup that we don't have control over.
    # This output goes to both stdout & stderr and can differ based on what
    # plugins are installed and various other factors. In order to reliably
    # capture only the scene_info, we write it out to another temp file.
    scene_info_fd, scene_info_file = tempfile.mkstemp()
    mel_script.write(_get_scene_info_mel_script(renderer, layers, scene_info_file))
    mel_script.flush()

    # Run Maya. This launches Maya, loads the scene file, runs our MEL wrapper
    # script, and exits.
    cmd = '%s -batch -script %s -file "%s"' % (_get_maya_bin_path(maya_version),
                                               mel_script.name,
                                               scene)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         shell=True)
    out, err = p.communicate()
    if p.returncode:
      raise Exception(
          'Maya failed to run. rc: [%d] stdout: [%s] stderr: [%s]' % (p.returncode, out, err))

    # Read in the scene info from file and clean up.
    with os.fdopen(scene_info_fd) as fp:
      scene_info_raw = fp.read()
    os.remove(scene_info_file)

  try:
    scene_info_from_scene = _clean_unicode_from_object(ast.literal_eval(scene_info_raw))
  except SyntaxError:
    print 'SyntaxError parsing scene_info.'
    print 'maya stdout: %s' % out
    print 'maya stderr: %s' % err
    raise

  return scene_info_from_scene


def main():
  logging.basicConfig(
      level=logging.INFO,
      format='%(asctime)s %(threadName)s %(module)s:%(lineno)d %(levelname)s %(message)s')

  parser = argparse.ArgumentParser(description=__doc__,
      formatter_class=argparse.RawTextHelpFormatter)
  parser.add_argument('--maya-version', required=True, help='Maya version number to test.')
  parser.add_argument('--scene', help='Path to the Maya scene to test.')
  parser.add_argument('--info-file', help='Path to JSON file containing expected scene info.')
  args = parser.parse_args()

  cmd = [_get_mayapy_path(args.maya_version),
         os.path.join(os.path.dirname(__file__), 'zync_maya_test.py')]
  if args.scene:
    cmd.extend(['--scene', args.scene, '--info-file', args.info_file])

  if not os.path.exists(cmd[0]):
      raise RuntimeError('Maya %s is not installed on this system.' % args.maya_version)

  subprocess.check_call(cmd)


if __name__ == '__main__':
  main()
