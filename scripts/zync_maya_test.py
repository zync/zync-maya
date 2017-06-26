"""Zync Maya Unit Tests.

This script is not meant to be executed directly; it must be run via mayapy
so the Maya Python environment is available to it.
"""

import argparse
import json
import os
import sys
import unittest

import zync_maya


class TestMayaScene(unittest.TestCase):
  """Scene-based tests, acting on an individual scene which must be provided."""

  def __init__(self, testname, scene_file, info_file):
    super(TestMayaScene, self).__init__(testname)
    self.scene_file = scene_file
    self.info_file = info_file
    # The scene JSON objects can get quite large and if there is a diff found
    # we want to display the whole thing for easier debugging.
    self.maxDiff = None

  def test_scene_info(self):
    with open(self.info_file) as fp:
      params = json.loads(fp.read())['params']
    scene_info_master = _unicode_to_str(params['scene_info'])

    # Would prefer not to import here, but you can't import maya.cmds before
    # running maya.standalone.initialize() and there's no reason to add the
    # Maya overhead for tests that aren't going to actually use maya.cmds.
    import maya.standalone
    maya.standalone.initialize()
    import maya.cmds

    # Assume the structure is <project folder>/scenes/<scene file>.
    maya.cmds.workspace(directory=os.path.dirname(os.path.dirname(self.scene_file)))
    maya.cmds.file(self.scene_file, force=True, open=True, ignoreVersion=True, prompt=False)
    scene_info_from_scene = _unicode_to_str(zync_maya.get_scene_info(
        params['renderer'], params['layers'].split(','), False, []))

    # Sort the file list from each set of scene info so we don't raise errors
    # caused only by file lists being in different orders.
    scene_info_from_scene['files'].sort()
    scene_info_master['files'].sort()

    # Be a bit less specific when checking renderer version.
    if 'arnold_version' in scene_info_from_scene:
      scene_info_from_scene['arnold_version'] = '.'.join(
          scene_info_from_scene['arnold_version'].split('.')[:2])
    if 'arnold_version' in scene_info_master:
      scene_info_master['arnold_version'] = '.'.join(
          scene_info_master['arnold_version'].split('.')[:2])

    if 'renderman_version' in scene_info_from_scene:
      scene_info_from_scene['renderman_version'] = (
          scene_info_from_scene['renderman_version'].split('.')[0])
    if 'renderman_version' in scene_info_master:
      scene_info_master['renderman_version'] = (
          scene_info_master['renderman_version'].split('.')[0])

    self.assertEqual(scene_info_from_scene, scene_info_master)


class TestMaya(unittest.TestCase):

  def test_replace_attr_tokens(self):
    self.assertEqual(
        zync_maya._replace_attr_tokens('/path/to/textures/<attr:path>/<attr:texture>'),
        '/path/to/textures/*/*')
    self.assertEqual(
        zync_maya._replace_attr_tokens('/path/to/textures/texture01.jpg'),
        '/path/to/textures/texture01.jpg')
    with self.assertRaises(zync_maya.MayaZyncException) as _:
      zync_maya._replace_attr_tokens('<attr:path>/<attr:texture>')


def _unicode_to_str(input_obj):
  """Returns a version of the input with all unicode replaced by standard
  strings.

  json.loads gives us unicode values, this method helps us clean that for
  easier comparison.

  Args:
    input_obj: whatever input you want to convert - dict, list, str, etc. will
               recurse into that object to convert all unicode values
  """
  if isinstance(input_obj, dict):
    return {_unicode_to_str(key): _unicode_to_str(value)
            for key, value in input_obj.iteritems()}
  elif isinstance(input_obj, list):
    return [_unicode_to_str(element) for element in input_obj]
  elif isinstance(input_obj, unicode):
    return input_obj.encode('utf-8')
  else:
    return input_obj


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__,
      formatter_class=argparse.RawTextHelpFormatter)
  parser.add_argument('--scene', help='Path to the Maya scene to test.')
  parser.add_argument('--info-file', help=('Path to JSON file containing '
                                           'expected scene information.'))
  args = parser.parse_args()

  if args.scene:
    if not args.info_file:
      print 'If you use --scene you must also use --info-file.'
      sys.exit(1)
    suite = unittest.TestSuite()
    suite.addTest(TestMayaScene('test_scene_info', args.scene, args.info_file))
  else:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMaya)
  test_result = unittest.TextTestRunner().run(suite)

  # Since we're not using unittest.main, we need to manually provide an exit
  # code or the script will report 0 even if the test failed. mayapy is buggy
  # and its shutdown procedure will often cause stack traces and bad exit
  # codes even when tests were successful. os._exit circumvents the normal
  # shutdown process so we can focus on the actual test result.
  os._exit(not test_result.wasSuccessful())
