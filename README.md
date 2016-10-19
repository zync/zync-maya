# Zync Plugin for Autodesk's Maya

Tested with Maya 2014, 2015, and 2016.

## zync-python

This plugin depends on zync-python, the Zync Python API.

Before trying to install zync-maya, make sure to [download zync-python](https://github.com/zync/zync-python) and follow the setup instructions there.

# Warning

Note that the simplest and recommended way to install Zync plugins is through the Zync Plugin Installer (see [instructions](https://sites.google.com/site/zyncpublic/doc/install#plugins)). The steps described below are for advanced users and we recommend to proceed with them only if you need to modify the plugin code for your custom needs.

## Clone the Repository

Clone this repository to the desired location on your local system. If you're doing a site-wide plugin install, this will have to be a location accessible by everyone using the plugins.

## Config File

Contained in this folder you'll find a file called ```config_maya.py.example```. Make a copy of this file in the same directory, and rename it ```config_maya.py```.

Edit ```config_maya.py``` in a Text Editor. It defines one config variable - `API_DIR` - the full path to your zync-python directory.

Set `API_DIR` to point to the zync-python you installed earlier, save the file, and close it.

## Maya.env

Now you'll need to point Maya to this folder to load it on startup.

The easiest way to do this is to create a Maya.env file and point it to this folder. See included Maya.env.example for an example of how to do this.

Your Maya.env will need to set the following two variables:

```
PYTHONPATH = Z:/path/to/plugins/zync-maya
XBMLANGPATH = Z:/path/to/plugins/zync-maya
```

Be careful; if Maya.env already exists it may be setting these variables already. If you see them elsewhere in the file, you'll need to append your path to the existing setting. For example:

PYTHONPATH = C:/my/scripts/folder;Z:/path/to/plugins/zync-maya

To separate paths, use a semicolon (;) on Windows and a colon (:) on Linux and Mac OS X.

**Linux Only** - XBMLANGPATH must end with "%B" to work:

```
PYTHONPATH = /usr/local/zync/plugins/zync-maya
XBMLANGPATH = /usr/local/zync/plugins/zync-maya/%B
```

For more information on setting up a Maya.env file, see the page "Setting environment variables using Maya.env" in the Maya Help Docs.
