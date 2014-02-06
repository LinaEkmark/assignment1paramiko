from datetime import datetime
import os
import sys

import alabaster


# Alabaster theme
html_theme_path = [alabaster.get_path()]
# Paths relative to invoking conf.py - not this shared file
html_static_path = ['../_shared_static']
html_theme = 'alabaster'
html_theme_options = {
    'description': "A Python implementation of SSHv2.",
    'github_user': 'paramiko',
    'github_repo': 'paramiko',
    'gittip_user': 'bitprophet',
    'analytics_id': 'UA-18486793-2',

    'link': '#3782BE',
    'link_hover': '#3782BE',
}
html_sidebars = {
    '**': [
        'about.html',
        'navigation.html',
        'searchbox.html',
        'donate.html',
    ]
}

# Regular settings
project = u'Paramiko'
year = datetime.now().year
copyright = u'2013-%d Jeff Forcier, 2003-2012 Robey Pointer' % year
master_doc = 'index'
templates_path = ['_templates']
exclude_trees = ['_build']
source_suffix = '.rst'
default_role = 'obj'
