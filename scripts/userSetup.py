import os
import sys

import zync_maya

import maya.cmds as cmds
import maya.mel
import maya.utils

def create_zync_shelf():
    maya.mel.eval('if (`shelfLayout -exists Zync `) deleteUI Zync;')
    shelfTab = maya.mel.eval('global string $gShelfTopLevel;')
    maya.mel.eval('global string $scriptsShelf;')
    maya.mel.eval('$scriptsShelf = `shelfLayout -p $gShelfTopLevel Zync`;')
    maya.mel.eval('shelfButton -parent $scriptsShelf -annotation "Render on Zync" ' + 
      '-label "Render on Zync" -image "zync.png" -sourceType "python" ' +
      '-command ("zync_maya.submit_dialog()") -width 34 -height 34 -style "iconOnly";')

maya.utils.executeDeferred( create_zync_shelf )
