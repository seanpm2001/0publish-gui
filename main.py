from xml.dom import Node, minidom

import rox, os, pango, sys, textwrap, traceback, subprocess, time, urlparse
from rox import g, tasks, loading
import gtk.glade

import signing
import archive
from implementation import ImplementationProperties
from requires import Requires
from xmltools import *

from zeroinstall.zerostore import unpack, Stores

RESPONSE_SAVE = 0
RESPONSE_SAVE_AND_TEST = 1

gladefile = os.path.join(rox.app_dir, '0publish-gui.glade')

# Zero Install implementation cache
stores = Stores()

def choose_feed():
	tree = gtk.glade.XML(gladefile, 'no_file_specified')
	box = tree.get_widget('no_file_specified')
	tree.get_widget('new_button').grab_focus()
	resp = box.run()
	box.destroy()
	if resp == 0:
		chooser = g.FileChooserDialog('Choose a location for the new feed',
					      None, g.FILE_CHOOSER_ACTION_SAVE)
		chooser.set_current_name('MyProg.xml')
		chooser.add_button(g.STOCK_CANCEL, g.RESPONSE_CANCEL)
		chooser.add_button(g.STOCK_NEW, g.RESPONSE_OK)
	elif resp == 1:
		chooser = g.FileChooserDialog('Choose the feed to edit',
					      None, g.FILE_CHOOSER_ACTION_OPEN)
		chooser.add_button(g.STOCK_CANCEL, g.RESPONSE_CANCEL)
		chooser.add_button(g.STOCK_OPEN, g.RESPONSE_OK)
	else:
		sys.exit(1)
	chooser.set_default_response(g.RESPONSE_OK)
	if chooser.run() != g.RESPONSE_OK:
		sys.exit(1)
	path = chooser.get_filename()
	chooser.destroy()
	return FeedEditor(path)

emptyFeed = """<?xml version='1.0'?>
<interface xmlns="%s">
  <name>Name</name>
</interface>
""" % (XMLNS_INTERFACE)

element_target = ('INTERNAL:FeedEditor/Element', gtk.TARGET_SAME_WIDGET, 0)

class FeedEditor(loading.XDSLoader):
	def __init__(self, pathname):
		loading.XDSLoader.__init__(self, None)

		self.pathname = pathname

		self.wTree = gtk.glade.XML(gladefile, 'main')
		self.window = self.wTree.get_widget('main')
		self.window.connect('destroy', rox.toplevel_unref)
		self.xds_proxy_for(self.window)

		help = gtk.glade.XML(gladefile, 'main_help')
		help_box = help.get_widget('main_help')
		help_box.set_default_size(g.gdk.screen_width() / 4,
				      g.gdk.screen_height() / 4)
		help_box.connect('delete-event', lambda box, ev: True)
		help_box.connect('response', lambda box, resp: box.hide())

		def resp(box, resp):
			if resp == g.RESPONSE_HELP:
				help_box.present()
			elif resp == RESPONSE_SAVE_AND_TEST:
				self.save(self.test)
			elif resp == RESPONSE_SAVE:
				self.save()
			else:
				box.destroy()
		self.window.connect('response', resp)
		rox.toplevel_ref()
	
		keys = signing.get_secret_keys()
		key_menu = self.wTree.get_widget('feed_key')
		key_model = g.ListStore(str, str)
		key_menu.set_model(key_model)
		cell = g.CellRendererText()
		cell.set_property('ellipsize', pango.ELLIPSIZE_MIDDLE)
		key_menu.pack_start(cell)
		key_menu.add_attribute(cell, 'text', 1)

		key_model.append((None, '(unsigned)'))
		for k in keys:
			key_model.append(k)

		self.impl_model = g.TreeStore(str, object)
		impl_tree = self.wTree.get_widget('impl_tree')
		impl_tree.set_model(self.impl_model)
		text = g.CellRendererText()
		column = g.TreeViewColumn('', text)
		column.add_attribute(text, 'text', 0)
		impl_tree.append_column(column)

		impl_tree.enable_model_drag_source(gtk.gdk.BUTTON1_MASK, [element_target], gtk.gdk.ACTION_MOVE)
		impl_tree.enable_model_drag_dest([element_target], gtk.gdk.ACTION_MOVE)

		sel = impl_tree.get_selection()
		sel.set_mode(g.SELECTION_BROWSE)

		if os.path.exists(self.pathname):
			data, _, self.key = signing.check_signature(self.pathname)
			self.doc = minidom.parseString(data)
			self.update_fields()
		else:
			self.doc = minidom.parseString(emptyFeed)
			self.key = None
			key_menu.set_active(0)

		root = self.impl_model.get_iter_root()
		if root:
			sel.select_iter(root)

		#self.attr_model = g.ListStore(str, str)
		#attributes = self.wTree.get_widget('attributes')
		#attributes.set_model(self.attr_model)
		#text = g.CellRendererText()
		#for title in ['Attribute', 'Value']:
		#	column = g.TreeViewColumn(title, text)
		#	attributes.append_column(column)
	
		self.wTree.get_widget('add_implementation').connect('clicked', lambda b: self.add_version())
		self.wTree.get_widget('add_archive').connect('clicked', lambda b: self.add_archive())
		self.wTree.get_widget('add_requires').connect('clicked', lambda b: self.add_requires())
		self.wTree.get_widget('add_group').connect('clicked', lambda b: self.add_group())
		self.wTree.get_widget('edit_properties').connect('clicked', lambda b: self.edit_version())
		self.wTree.get_widget('remove').connect('clicked', lambda b: self.remove_version())
		impl_tree.connect('row-activated', lambda tv, path, col: self.edit_version(path))
		impl_tree.connect('drag-data-received', self.tree_drag_data_received)

		self.wTree.get_widget('notebook').next_page()

	def tree_drag_data_received(self, treeview, context, x, y, selection, info, time):
		if not selection: return
		drop_info = treeview.get_dest_row_at_pos(x, y)
		if drop_info:
			model = treeview.get_model()
			path, position = drop_info

			src = self.get_selected()
			dest = model[path][1]

			def is_ancestor_or_self(a, b):
				while b:
					if b is a: return True
					b = b.parentNode
				return False

			if is_ancestor_or_self(src, dest):
				# Can't move an element into itself!
				return

			if position in (gtk.TREE_VIEW_DROP_BEFORE, gtk.TREE_VIEW_DROP_AFTER):
				new_parent = dest.parentNode
			else:
				new_parent = dest

			if src.namespaceURI != XMLNS_INTERFACE: return
			if new_parent.namespaceURI != XMLNS_INTERFACE: return

			if new_parent.localName in ('group', 'interface'):
				if src.localName not in ('implementation', 'group'):
					return
			elif new_parent.localName == 'implementation':
				if src.localName not in ['requires']:
					return
			else:
				return

			remove_element(src)

			if position == gtk.TREE_VIEW_DROP_BEFORE:
				insert_before(src, dest)
			elif position == gtk.TREE_VIEW_DROP_AFTER:
				next = dest.nextSibling
				while next and not next.nodeType == Node.ELEMENT_NODE:
					next = next.nextSibling
				if next:
					insert_before(src, next)
				else:
					insert_element(src, new_parent)
			else:
				insert_element(src, new_parent)
			self.update_version_model()

	def tree_drag_data_get(self, tv, context, data, info, time):
		if info != element_target[2]:
			return
		print "get"

	def add_version(self):
		ImplementationProperties(self)

	def add_group(self):
		ImplementationProperties(self, is_group = True)

	def add_requires(self):
		elem = self.get_selected()
		if elem.namespaceURI == XMLNS_INTERFACE:
			if elem.localName in ('group', 'implementation'):
				Requires(self, parent = elem)
				return
			elif elem.localName == 'requires':
				Requires(self, parent = elem.parentNode, element = elem)
				return
		rox.alert('Select a group, implementation or requirement!')

	def edit_version(self, path = None, element = None):
		assert not (path and element)

		if element:
			pass
		elif path is None:
			element = self.get_selected()
		else:
			element = self.impl_model[path][1]

		ImplementationProperties(self, element)
	
	def update_fields(self):
		root = self.doc.documentElement

		def set(name):
			value = singleton_text(root, name)
			if value:
				self.wTree.get_widget('feed_' + name).set_text(value)
		set('name')
		set('summary')
		set('homepage')

		for icon in children(root, 'icon'):
			if icon.getAttribute('type') == 'image/png':
				href = icon.getAttribute('href')
				self.wTree.get_widget('feed_icon').set_text(href)
				break

		description = singleton_text(root, 'description') or ''
		paragraphs = [format_para(p) for p in description.split('\n\n')]
		buffer = self.wTree.get_widget('feed_description').get_buffer()
		buffer.delete(buffer.get_start_iter(), buffer.get_end_iter())
		buffer.insert_at_cursor('\n'.join(paragraphs))

		key_menu = self.wTree.get_widget('feed_key')
		model = key_menu.get_model()
		if self.key:
			i = 0
			for line in model:
				if line[0] == self.key:
					break
				i += 1
			else:
				model.append((self.key, 'Missing key (%s)' % self.key))
			key_menu.set_active(i)
		else:
			key_menu.set_active(0)

		self.update_version_model()
	
	def add_archives(self, impl_element, iter):
		for child in child_elements(impl_element):
			if child.namespaceURI != XMLNS_INTERFACE: continue
			if child.localName == 'archive':
				self.impl_model.append(iter, ['Archive ' + child.getAttribute('href'), child])
			else:
				self.impl_model.append(iter, ['<%s>' % child.localName, child])
	
	def update_version_model(self):
		self.impl_model.clear()
		impl_tree = self.wTree.get_widget('impl_tree')
		to_expand = []

		def add_impls(elem, iter, attrs):
			"""Add all groups, implementations and requirements in elem"""

			for x in child_elements(elem):
				if x.namespaceURI != XMLNS_INTERFACE: continue

				if x.localName == 'requires':
					req_iface = x.getAttribute('interface')
					new = self.impl_model.append(iter, ['Requires %s' % req_iface, x])

				if x.localName not in ('implementation', 'group'): continue

				new_attrs = attrs.copy()
				attributes = x.attributes
				for i in range(attributes.length):
					a = attributes.item(i)
					new_attrs[str(a.name)] = a.value

				if x.localName == 'implementation':
					version = new_attrs.get('version', '(missing version number)')
					new = self.impl_model.append(iter, ['Version %s' % version, x])
					self.add_archives(x, new)
				elif x.localName == 'group':
					new = self.impl_model.append(iter, ['Group', x])
					to_expand.append(self.impl_model.get_path(new))
					add_impls(x, new, new_attrs)
					
		add_impls(self.doc.documentElement, None, attrs = {})

		for path in to_expand:
			impl_tree.expand_row(path, False)

	def test(self):
		child = os.fork()
		if child == 0:
			try:
				try:
					# We are the child
					# Spawn a grandchild and exit
					subprocess.Popen(['0launch', '--gui', self.pathname])
					os._exit(0)
				except:
					traceback.print_exc()
			finally:
				os._exit(1)
		pid, status = os.waitpid(child, 0)
		assert pid == child
		if status:
			raise Exception('Failed to run 0launch - status code %d' % status)
	
	def update_doc(self):
		root = self.doc.documentElement
		def update(name, required = False, attrs = {}, value_attr = None):
			widget = self.wTree.get_widget('feed_' + name)
			if isinstance(widget, g.TextView):
				buffer = widget.get_buffer()
				text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter())
				paras = ['\n'.join(textwrap.wrap(para, 80)) for para in text.split('\n') if para.strip()]
				value = '\n' + '\n\n'.join(paras)
			else:
				value = widget.get_text()
			elems = list(children(root, name, attrs = attrs))
			if value:
				if elems:
					elem = elems[0]
				else:
					elem = create_element(root, name,
							        before = ['group', 'implementation', 'requires'])
					for x in attrs:
						elem.setAttribute(x, attrs[x])
				if value_attr:
					elem.setAttribute(value_attr, value)
					set_data(elem, None)
				else:
					set_data(elem, value)
			else:
				if required:
					raise Exception('Missing required field "%s"' % name)
				for e in elems:
					remove_element(e)
			
		update('name', True)
		update('summary', True)
		update('description', True)
		update('homepage')
		update('icon', attrs = {'type': 'image/png'}, value_attr = 'href')

		uri = self.wTree.get_widget('feed_url').get_text()
		if uri:
			root.setAttribute('uri', uri)
		elif root.hasAttribute('uri'):
			root.removeAttribute('uri')

		key_menu = self.wTree.get_widget('feed_key')
		key_model = key_menu.get_model()
		self.key = key_model[key_menu.get_active()][0]
	
	def save(self, callback = None):
		self.update_doc()
		if self.key:
			sign = signing.sign_xml
		else:
			sign = signing.sign_unsigned
		data = self.doc.toxml() + '\n'

		gen = sign(self.pathname, data, self.key, callback)
		# May require interaction to get the pass-phrase, so run in the background...
		if gen:
			tasks.Task(gen)

	def add_archive(self):
		archive.AddArchiveBox(self)
	
	def xds_load_from_file(self, path):
		archive.AddArchiveBox(self, local_archive = path)
	
	def remove_version(self, path = None):
		elem = self.get_selected()
		remove_element(elem)
		self.update_version_model()
	
	def get_selected(self):
		tree = self.wTree.get_widget('impl_tree')
		sel = tree.get_selection()
		model, iter = sel.get_selected()
		if not iter:
			raise Exception('Select something first!')
		return model[iter][1]

	def find_implementation(self, id):
		def find_impl(parent):
			for x in child_elements(parent):
				if x.namespaceURI != XMLNS_INTERFACE: continue
				if x.localName == 'group':
					sub = find_impl(x)
					if sub: return sub
				elif x.localName == 'implementation':
					if x.getAttribute('id') == id:
						return x
		return find_impl(self.doc.documentElement)
