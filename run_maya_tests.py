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
import logging
import os
import platform
import subprocess


def _get_maya_bin(maya_version):
  """Gets Maya install location."""
  # No Windows support here at the moment.
  if platform.system() == 'Darwin':
    return '/Applications/Autodesk/maya%s/Maya.app/Contents/bin/mayapy' % maya_version
  else:
    # Prefer Maya I/O if it's installed.
    if os.path.isdir('/usr/autodesk/mayaIO%s' % maya_version):
      return '/usr/autodesk/mayaIO%s/bin/mayapy' % maya_version
    return '/usr/autodesk/maya%s/bin/mayapy' % maya_version


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

  cmd = [_get_maya_bin(args.maya_version),
         os.path.join(os.path.dirname(__file__), 'zync_maya_test.py')]
  if args.scene:
    cmd.extend(['--scene', args.scene, '--info-file', args.info_file])

  if not os.path.exists(cmd[0]):
      raise RuntimeError('Maya %s is not installed on this system.' % args.maya_version)

  subprocess.check_call(cmd)


if __name__ == '__main__':
  main()
