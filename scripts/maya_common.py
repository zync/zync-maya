"""
Zync Maya Plugin - Common
"""
import re

# Regex string for checking if string contains a layer token.
_HAS_LAYER_TOKEN_RE = re.compile(r'.*%l.*|.*<layer>.*|.*<renderlayer>.*', re.IGNORECASE)
_SUBSTITUTE_LAYER_TOKEN_RE = re.compile(r'%l|<layer>|<renderlayer>', re.IGNORECASE)
_SUBSTITUTE_CAMERA_TOKEN_RE = re.compile(r'%c|<camera>', re.IGNORECASE)
_SUBSTITUTE_SCENE_TOKEN_RE = re.compile(r'%s|<scene>', re.IGNORECASE)

class MayaZyncException(Exception):
  pass


class ZyncAbortedByUser(Exception):
  """
  Exception to handle user's decision about canceling a process.
  """
  pass


class ZyncSubmissionCheckError(Exception):
  """
  Exception to handle errors when trying to run a SubmissionCheck.
  """
  pass
