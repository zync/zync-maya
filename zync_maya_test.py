#!/usr/bin/env python
"""Test a Maya scene against the Zync Maya plugin.

Accepts a path to the Maya scene to test, and a JSON file containing expected scene
information. The structure of the file must be:

{
  "params": {
    "renderer": <str, renderer to use e.g. vray>,
    "layers": <str, comma-separated list of render layers to be rendered>,
    "scene_info": <dict, expected scene info>
  }
}

The file can contain other information as well, which will be ignored.
"""

import argparse
import ast
import json
import logging
import os
import platform
import pprint
import subprocess
import tempfile
import unittest


def _get_maya_bin():
  """Gets Maya install location."""
  # mac
  if platform.system() == 'Darwin':
    return '/Applications/Autodesk/maya2016/Maya.app/Contents/bin/maya'
  # linux. testing on windows not currently supported.
  else:
    if os.path.isdir('/usr/autodesk/mayaIO2016'):
      return '/usr/autodesk/mayaIO2016/bin/maya'
    return '/usr/autodesk/maya2016/bin/maya'


def _unicode_to_str(input):
  """Returns a version of the input with all unicode replaced by standard
  strings.

  json.loads gives us unicode values, this method helps us clean that for
  easier comparison.

  Args:
    input: whatever input you want to convert - dict, list, str, etc. will
           recurse into that object to convert all unicode values
  """
  if isinstance(input, dict):
    return {_unicode_to_str(key): _unicode_to_str(value)
            for key, value in input.iteritems()}
  elif isinstance(input, list):
    return [_unicode_to_str(element) for element in input]
  elif isinstance(input, unicode):
    return input.encode('utf-8')
  else:
    return input


class TestMayaScene(unittest.TestCase):

  def __init__(self, testname, scene_file, info_file):
    super(TestMayaScene, self).__init__(testname)
    self.scene_file = scene_file
    self.info_file = info_file
    # test_scene_info compares dicts, setting maxDiff to None tells it
    # to show the entire diff in case of a mismatch.
    self.maxDiff = None

  def test_scene_info(self):
    with open(self.info_file) as fp:
      params = json.loads(fp.read())['params']
    scene_info_master = _unicode_to_str(params['scene_info'])

    # Write out a temporary MEL script which wraps the call to zync-maya.
    # We could use mayapy instead but mayapy has proven unreliable in initializing
    # its environment in the same way as standard maya.
    with tempfile.NamedTemporaryFile() as mel_script:
      # Maya produces a lot of output on startup that we don't have control over.
      # This output goes to both stdout & stderr and can differ based on what plugins are
      # installed and various other factors. In order to reliably capture only the
      # scene_info, we write it out to another temp file.
      scene_info_fd, scene_info_file = tempfile.mkstemp()
      script_text = 'python("import zync_maya"); '
      script_text += 'string $scene_info = python("zync_maya.get_scene_info('
      # renderer
      script_text += '\'%s\', ' % params['renderer']
      # list of layers being rendered. this comes in a comma-separated string already
      # so no need to join
      script_text += '[\'%s\'], ' % params['layers']
      # is_bake
      script_text += 'False)"); '
      script_text += 'string $output_file = "%s"; ' % scene_info_file
      script_text += '$fp = `fopen $output_file "w"`; '
      script_text += 'fprint $fp $scene_info; '
      script_text += 'fclose $fp; '
      mel_script.write(script_text)
      mel_script.flush()

      # Run Maya. This launches Maya, loads the scene file, runs our MEL wrapper
      # script, and exits.
      cmd = '%s -batch -script %s -file "%s"' % (_get_maya_bin(), mel_script.name, self.scene_file)
      p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
      p.communicate()

      # Read in the scene info from file and clean up.
      with os.fdopen(scene_info_fd) as fp:
        scene_info_raw = fp.read()
      os.remove(scene_info_file)

    scene_info_from_scene = _unicode_to_str(ast.literal_eval(scene_info_raw))

    # sort the file list from each set of scene info so we don't raise errors
    # caused only by file lists being in different orders
    scene_info_from_scene['files'].sort()
    scene_info_master['files'].sort()

    self.assertEqual(scene_info_from_scene, scene_info_master)


def main():
  logging.basicConfig(
      level=logging.INFO,
      format='%(asctime)s %(threadName)s %(module)s:%(lineno)d %(levelname)s %(message)s')

  parser = argparse.ArgumentParser(description=__doc__,
      formatter_class=argparse.RawTextHelpFormatter)
  parser.add_argument('--scene', required=True, help='Path to the Maya scene to test.')
  parser.add_argument('--info-file', required=True, help=('Path to JSON file containing '
                                                          'expected scene information.'))
  args = parser.parse_args()

  suite = unittest.TestSuite()
  suite.addTest(TestMayaScene('test_scene_info', args.scene, args.info_file))
  unittest.TextTestRunner().run(suite)


if __name__ == '__main__':
  main()
