"""Dialog to sekect files or directories
"""

import glob
import os

# Try importing from PySide (Maya 2016) first, then from PySide2 (Maya 2017)
# Alias the classes since some of them have been moved from PyGui to PyWidgets
try:
  import pysideuic
  import PySide.QtGui
  import PySide.QtCore as QtCore

  QDialog = PySide.QtGui.QDialog
  QDialogButtonBox = PySide.QtGui.QDialogButtonBox
  QDirModel = PySide.QtGui.QDirModel
  QTreeView = PySide.QtGui.QTreeView
except:
  import pyside2uic as pysideuic
  import PySide2.QtCore as QtCore
  import PySide2.QtWidgets

  QDialog = PySide2.QtWidgets.QDialog
  QDialogButtonBox = PySide2.QtWidgets.QDialogButtonBox
  QDirModel = PySide2.QtWidgets.QDirModel
  QTreeView = PySide2.QtWidgets.QTreeView

import xml.etree.ElementTree as ElementTree

from cStringIO import StringIO

UI_SELECT_FILES = '%s/resources/select_files_dialog.ui' % (os.path.dirname(__file__),)

class CheckableDirModel(QDirModel):
  """Extends QDirModel by adding checkboxes next to files and
  directories. Stores the files and directories selected."""

  def __init__(self):
    QDirModel.__init__(self, None)
    self.files = {}

  def get_selected_files(self, selected_files):
    selected_files.clear()
    for full_name in self.files:
      if self.files[file] == QtCore.Qt.Checked:
        selected_files.add(full_name)

  def flags(self, index):
    return QDirModel.flags(self, index) | QtCore.Qt.ItemIsUserCheckable

  def data(self, index, role=QtCore.Qt.DisplayRole):
    if role != QtCore.Qt.CheckStateRole:
      return QDirModel.data(self, index, role)
    else:
      if index.column() == 0:
        filename = self.filePath(index)
        if filename in self.files:
          value = self.files[filename]
          if value == QtCore.Qt.PartiallyChecked:
            return self._getCheckStatusDown(filename)
          else:
            return value
        else:
          return self._getCheckStatusUp(filename)

  def setData(self, index, value, role):
    if (role == QtCore.Qt.CheckStateRole and index.column() == 0):
      filename = self.filePath(index)
      self._checkUp(filename)
      self._clearDown(filename)

      if filename in self.files:
        del self.files[filename]
      else:
        self.files[filename] = QtCore.Qt.Checked
      self.emit(QtCore.SIGNAL("dataChanged(QModelIndex,QModelIndex)"), None, None)

      return True
    else:
      return QDirModel.setData(self, index, value, role)

  def _getCheckStatusUp(self, filename):
    dirname = os.path.dirname(filename)
    if dirname == filename or not dirname:
      return QtCore.Qt.Unchecked
    else:
      if dirname in self.files:
        if self.files[dirname] == QtCore.Qt.Checked:
          return QtCore.Qt.Checked
    return self._getCheckStatusUp(dirname)

  def _getCheckStatusDown(self, filename):
    num_checked = 0
    full_names = glob.glob(os.path.join(filename, '*'))
    for full_name in full_names:
      if full_name in self.files:
        if self.files[full_name] == QtCore.Qt.Checked:
          num_checked += 1
        elif self.files[full_name] == QtCore.Qt.PartiallyChecked:
          result = self._getCheckStatusDown(full_name)
          if result == QtCore.Qt.PartiallyChecked:
            return QtCore.Qt.PartiallyChecked
          elif result == QtCore.Qt.Checked:
            num_checked += 1

    if num_checked == 0:
      return QtCore.Qt.Unchecked
    elif num_checked == len(full_names):
      return QtCore.Qt.Checked
    else:
      return QtCore.Qt.PartiallyChecked

  def _checkUp(self, filename):
    dirname = os.path.dirname(filename)
    if dirname == filename or not dirname:
      return
    self.files[dirname] = QtCore.Qt.PartiallyChecked
    self._checkUp(dirname)

  def _clearDown(self, filename):
    if not filename in self.files or not os.path.isdir(filename):
      return
    full_names = glob.glob(os.path.join(filename, '*'))
    for full_name in full_names:
      if full_name in self.files:
        del self.files[full_name]
      self._clearDown(full_name)


class FileSelectDialog(object):
  """Displays dialog allowing user to select files or directories"""
  def __init__(self, selected_files):
    """Constructs file selection dialog

    Params:
      selected_file: set, set to store selected files upon success
    """
    self.selected_files = selected_files

    FormClass = _load_ui_type(UI_SELECT_FILES)
    form_class = FormClass()
    self.dialog = QDialog()
    form_class.setupUi(self.dialog)

    self.model = CheckableDirModel()
    tree_view = self.dialog.findChild(QTreeView, 'listDirsFiles')
    tree_view.setModel(self.model)

    button_box = self.dialog.findChild(QDialogButtonBox, 'buttonBox')
    button_box.accepted.connect(self.accepted)
    button_box.rejected.connect(self.rejected)

  def accepted(self):
    self.model.get_selected_files(self.selected_files)
    self.dialog.destroy()

  def rejected(self):
    self.dialog.destroy()

  def show(self):
    self.dialog.show()

def _load_ui_type(filename):
  """Loads and parses ui file created by Qt Designer"""
  xml = ElementTree.parse(filename)
  # pylint: disable=no-member
  form_class = xml.find('class').text

  with open(filename, 'r') as ui_file:
    output_stream = StringIO()
    frame = {}

    pysideuic.compileUi(ui_file, output_stream, indent=0)
    compiled = compile(output_stream.getvalue(), '<string>', 'exec')
    # pylint: disable=exec-used
    exec compiled in frame

    form_class = frame['Ui_%s'%form_class]

  return form_class

