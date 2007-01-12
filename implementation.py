from xml.dom import Node, minidom

import rox, os, pango, sys, textwrap, traceback, subprocess, time, urlparse
from rox import g, tasks, loading
import gtk.glade

import main
from xmltools import *

from zeroinstall.zerostore import unpack, NotStored

def get_combo_value(combo):
	i = combo.get_active()
	m = combo.get_model()
	return m[i][0]

class ImplementationProperties:
	def __init__(self, feed_editor, element = None, is_group = False):
		self.feed_editor = feed_editor
		self.element = element

		widgets = gtk.glade.XML(main.gladefile, 'version')

		attributes = g.ListStore(str, str)
		attr_view = widgets.get_widget('attributes')
		attr_view.set_model(attributes)

		attr_view.append_column(g.TreeViewColumn('Name'))
		attr_view.append_column(g.TreeViewColumn('Value'))

		inherit_arch = widgets.get_widget('inherit_arch')
		def shade_os_cpu():
			s = not inherit_arch.get_active()
			widgets.get_widget('cpu').set_sensitive(s)
			widgets.get_widget('os').set_sensitive(s)
		shade_os_cpu()
		inherit_arch.connect('toggled', lambda cb: shade_os_cpu())

		main_menu = widgets.get_widget('main_binary')

		if element:
			if element.localName == 'group':
				is_group = True
				id = None
			else:
				id = element.getAttribute('id')

			widgets.get_widget('version_number').set_text(element.getAttribute('version'))
			widgets.get_widget('released').set_text(element.getAttribute('released'))

			main_binary = element.getAttribute('main')

			stability_menu = widgets.get_widget('stability')
			stability = element.getAttribute('stability')
			if stability:
				i = 0
				for row in stability_menu.get_model():
					if row[0].lower() == stability:
						stability_menu.set_active(i)
						break
					i += 1
			else:
				stability_menu.set_active(0)

			def ok():
				self.update_impl(element, widgets)
		else:
			released = widgets.get_widget('released')

			id = None
			if is_group:
				widgets.get_widget('version_number').set_text('')
				released.set_text('')
			else:
				widgets.get_widget('version_number').set_text('1.0')
				released.set_text(time.strftime('%Y-%m-%d'))

			widgets.get_widget('cpu').set_active(0)
			widgets.get_widget('os').set_active(0)
			widgets.get_widget('stability').set_active(0)
			main_binary = None

			def ok():
				if is_group:
					element_name = 'group'
				else:
					element_name = 'implementation'
				element = create_element(self.feed_editor.doc.documentElement, element_name)
				self.update_impl(element, widgets)

		self.is_group = is_group

		if is_group:
			widgets.get_widget('id_label').set_text('(group)')
		elif id:
			widgets.get_widget('id_label').set_text(id)
		else:
			widgets.get_widget('-')

		def resp(dialog, r):
			if r == g.RESPONSE_OK:
				ok()
				self.feed_editor.update_version_model()
			dialog.destroy()

		if is_group and element:
			# Find a cached implementation for getting main
			for x in child_elements(element):
				if x.localName == 'implementation' and x.namespaceURI == XMLNS_INTERFACE:
					id = x.getAttribute('id')
					try:
						if id and main.stores.lookup(id):
							break
					except NotStored, ex:
						pass
		if id:
			# Find possible main settings, if possible
			try:
				cached_impl = main.stores.lookup(id)
			except NotStored, ex:
				pass
			else:
				i = 0
				for (dirpath, dirnames, filenames) in os.walk(cached_impl):
					for file in filenames:
						info = os.stat(os.path.join(dirpath, file))
						if info.st_mode & 0111:
							relbasedir = dirpath[len(cached_impl) + 1:]
							new = os.path.join(relbasedir, file)
							main_menu.append_text(new)
							if new == main_binary:
								main_binary = None
								main_menu.set_active(i)
							i += 1
		if main_binary:
			main_menu.append_text(main_binary)
			main_menu.set_active(i)

		dialog = widgets.get_widget('version')
		dialog.connect('response', resp)

	def update_impl(self, element, widgets):
		version = widgets.get_widget('version_number').get_text()
		released = widgets.get_widget('released').get_text()
		inherit_arch = widgets.get_widget('inherit_arch')

		def get_combo(name):
			widget = widgets.get_widget(name)
			return get_combo_value(widget)

		cpu = get_combo('cpu')
		os = get_combo('os')

		widget = widgets.get_widget('stability')
		if widget.get_active() == 0:
			stability = None
		else:
			stability = get_combo('stability').lower()

		if inherit_arch.get_active():
			arch = None
		else:
			arch = os + '-' + cpu

		main = widgets.get_widget('main_binary').get_active_text()

		for name, value in [('version', version),
			            ('arch', arch),
			            ('main', main),
			            ('released', released),
			            ('stability', stability)]:
			if value:
				element.setAttribute(name, value)
			elif element.hasAttribute(name):
				element.removeAttribute(name)