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
        params['renderer'], params['layers'].split(','), False, [],
        zync_maya.parse_frame_range(params['frange'])))

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
  """Scene-less tests."""

  def test_replace_attr_tokens(self):
    self.assertEqual(
        zync_maya._replace_attr_tokens('/path/to/textures/<attr:path>/<attr:texture>'),
        '/path/to/textures/*/*')
    self.assertEqual(
        zync_maya._replace_attr_tokens('/path/to/textures/texture01.jpg'),
        '/path/to/textures/texture01.jpg')
    with self.assertRaises(zync_maya.MayaZyncException) as _:
      zync_maya._replace_attr_tokens('<attr:path>/<attr:texture>')

  def test_maya_attr_is_true(self):
    self.assertEqual(zync_maya._maya_attr_is_true(True), True)
    self.assertEqual(zync_maya._maya_attr_is_true(False), False)
    self.assertEqual(zync_maya._maya_attr_is_true([True, True]), True)
    self.assertEqual(zync_maya._maya_attr_is_true([True, False]), True)
    self.assertEqual(zync_maya._maya_attr_is_true([False, False]), False)
    self.assertEqual(zync_maya._maya_attr_is_true(self._generate_true_vals()), True)
    self.assertEqual(zync_maya._maya_attr_is_true(self._generate_false_vals()), False)

  def _generate_true_vals(self):
    for i in range(3):
      yield True

  def _generate_false_vals(self):
    for i in range(3):
      yield False

  def test_parse_frame_range(self):
    self.assertEqual(zync_maya.parse_frame_range('92'), [92])
    self.assertEqual(zync_maya.parse_frame_range('-5'), [-5])
    self.assertEqual(zync_maya.parse_frame_range('23-26'), [23, 24, 25, 26])
    self.assertEqual(zync_maya.parse_frame_range('-5--3'), [-5, -4, -3])
    self.assertEqual(zync_maya.parse_frame_range('-1-2'), [-1, 0, 1, 2])
    self.assertEqual(zync_maya.parse_frame_range('45-42'), [45, 44, 43, 42])
    self.assertEqual(zync_maya.parse_frame_range('-97--99'), [-97, -98, -99])
    self.assertEqual(zync_maya.parse_frame_range('1--2'), [1, 0, -1, -2])
    self.assertEqual(zync_maya.parse_frame_range('1,57'), [1, 57])
    self.assertEqual(zync_maya.parse_frame_range('5,23-25'), [5, 23, 24, 25])
    with self.assertRaises(ValueError) as _:
      zync_maya.parse_frame_range('notAFrameRange')

  def test_extract_frame_num(self):
    self.assertEqual(
        zync_maya.extract_frame_number_from_file_path('/path/to/file.2763.exr'), 2763)
    self.assertEqual(
        zync_maya.extract_frame_number_from_file_path('/path/to/file.0001.exr'), 1)
    self.assertEqual(
        zync_maya.extract_frame_number_from_file_path('/path/to/singleFile.txt'), None)
    self.assertEqual(
        zync_maya.extract_frame_number_from_file_path('/path/to.2734.dir/file.png'), None)
    self.assertEqual(
        zync_maya.extract_frame_number_from_file_path('/path/to.2734.dir/file.9673.png'), 9673)
    self.assertEqual(
        zync_maya.extract_frame_number_from_file_path('/path/to/file_07.0283.exr'), 283)


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
