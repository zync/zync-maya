# Zync Plugin for Autodesk's Maya

**Notice:** this project has been archived and is no longer being maintained.

For a list of Maya versions supported on Zync please see [our main website](https://www.zyncrender.com/#about). You can find additional info in [our documentation](https://docs.zyncrender.com/faq#q-what-applicationrendererplugin-versions-do-you-support).

## zync-python

This plugin depends on zync-python, the Zync Python API.

Before trying to install zync-maya, make sure to [download zync-python](https://github.com/zync/zync-python) and follow the setup instructions there.

# Warning

Note that the simplest and recommended way to install Zync plugins is through the Zync Client Application (see [instructions](https://docs.zyncrender.com/install-and-setup#option-1-the-plugins-tab-in-the-zync-client-app-simple-recommended-for-most-users)). The steps described below are for advanced users and we recommend to proceed with them only if you need to modify the plugin code for your custom needs.

## Clone the Repository

Clone this repository to the desired location on your local system. If you're doing a site-wide plugin install, this will have to be a location accessible by everyone using the plugins.

## Config File

Contained in `scripts/` you'll find a file called ```config_maya.py.example```. Make a copy of this file in the same directory, and rename it ```config_maya.py```.

Edit ```config_maya.py``` in a Text Editor. It defines one config variable - `API_DIR` - the full path to your zync-python directory.

Set `API_DIR` to point to the zync-python you installed earlier, save the file, and close it.

## zync.mod

Now you'll need to point Maya to this folder to load it on startup.

Create a file named `zync.mod` with the following contents:

```
+ zync 1.0 Z:/path/to/plugins/zync-maya
```

This file can be placed anywhere within Maya's module search path. The module search path is defined by the `MAYA_MODULE_PATH` environment settings, as described in [the Maya docs](https://knowledge.autodesk.com/support/maya/learn-explore/caas/CloudHelp/cloudhelp/2016/ENU/Maya/files/GUID-228CCA33-4AFE-4380-8C3D-18D23F7EAC72-htm.html).

You can view your `MAYA_MODULE_PATH` setting by running the following in the Maya Script Editor:

```
getenv MAYA_MODULE_PATH
```

Once `zync.mod` is in place, restart Maya. You should now see a "Zync" shelf with the Zync icon present.

