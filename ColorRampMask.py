bl_info = {
    "name": "ColorRamp Mask",
    "author": "Yolu",
    "version": (2, 12, 0),
    "blender": (3, 4, 0),
    "location": "Shader Editor > Sidebar (N) > ColorRamp Mask",
    "description": "Make an existing ColorRamp Into Mask Which You can Control",
    "category": "Node",
}

import bpy


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def find_socket(sockets, identifier):
    for s in sockets:
        if s.identifier == identifier:
            return s
    return None


def interface_has_input(tree, name):
    if hasattr(tree, "interface"):  # 4.0+
        for item in tree.interface.items_tree:
            if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == name:
                return True
        return False
    return name in tree.inputs


def get_or_create_panel(tree, name):
    """Collapsible panel on the group interface (Blender 4.0+). Returns None if unsupported."""
    iface = getattr(tree, "interface", None)
    if iface is None or not hasattr(iface, "new_panel"):
        return None
    try:
        for item in iface.items_tree:
            if item.item_type == 'PANEL' and item.name == name:
                return item
        return iface.new_panel(name=name, default_closed=False)
    except Exception as e:
        print("ColorRamp Mask: could not create panel", name, e)
        return None


def add_interface_color(tree, name, default_color, parent=None):
    """Add a Color input to the group (skips if it already exists)."""
    if interface_has_input(tree, name):
        return
    try:
        if hasattr(tree, "interface"):
            kw = {"parent": parent} if parent is not None else {}
            item = tree.interface.new_socket(name=name, in_out='INPUT',
                                             socket_type='NodeSocketColor', **kw)
        else:
            item = tree.inputs.new('NodeSocketColor', name)
        item.default_value = default_color
    except Exception as e:  # default value is only a nicety
        print(" ColorRamp Mask: could not set default for", name, e)


def iter_all_node_trees():
    seen = set()
    colls = [bpy.data.node_groups, bpy.data.materials, bpy.data.worlds, bpy.data.lights]
    for coll in colls:
        for idb in coll:
            t = idb if isinstance(idb, bpy.types.NodeTree) else getattr(idb, "node_tree", None)
            if t is not None and t.as_pointer() not in seen:
                seen.add(t.as_pointer())
                yield t


def push_color_defaults(tree, name, color):
    """Set the color on the interface default AND on every Group node using this tree."""
    # interface default (used for newly added group nodes)
    try:
        if hasattr(tree, "interface"):
            for item in tree.interface.items_tree:
                if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == name:
                    item.default_value = color
        else:
            tree.inputs[name].default_value = color
    except Exception as e:
        print(" ColorRamp Mask: interface default failed", name, e)

    # existing group node instances (these keep their own values, default white)
    for t in iter_all_node_trees():
        for n in t.nodes:
            if n.type == 'GROUP' and same_tree(n.node_tree, tree):
                sock = n.inputs.get(name)
                if sock is not None:
                    try:
                        sock.default_value = color
                    except Exception:
                        pass


def is_node_group(tree):
    """True when the tree is a real Node Group (not a material's embedded tree)."""
    if getattr(tree, "is_embedded_data", False):
        return False
    return True


def same_tree(a, b):
    return a is not None and b is not None and a.as_pointer() == b.as_pointer()


def real_inputs(node):
    return [x for x in node.inputs if x.bl_idname != 'NodeSocketVirtual']


def add_group_output_socket(tree, name="Result"):
    try:
        if hasattr(tree, "interface"):
            tree.interface.new_socket(name=name, in_out='OUTPUT', socket_type='NodeSocketColor')
        else:
            tree.outputs.new('NodeSocketColor', name)
    except Exception as e:
        print("ColorRamp Mask: could not add output socket:", e)


def ensure_group_output(tree, from_socket, x, y):
    """Link from_socket to a Group Output (creates node / socket when missing).
    Returns True only if a link to the Group Output really exists afterwards."""
    outs = [n for n in tree.nodes if n.type == 'GROUP_OUTPUT']
    out_node = None
    for n in outs:
        if getattr(n, "is_active_output", False):
            out_node = n
            break
    if out_node is None and outs:
        out_node = outs[0]
    if out_node is None:
        out_node = tree.nodes.new('NodeGroupOutput')
        out_node.location = (x, y)

    def pick_target():
        socks = real_inputs(out_node)
        for sk in socks:
            if sk.name == "Result" and sk.type == 'RGBA':
                return sk
        for sk in socks:
            if sk.type == 'RGBA':
                return sk
        return None

    target = pick_target()
    if target is None:
        add_group_output_socket(tree, "Result")
        refresh_interface(tree)
        target = pick_target()
    if target is None:
        # last resort: dropping a link on the empty (virtual) socket creates the output
        virt = [sk for sk in out_node.inputs if sk.bl_idname == 'NodeSocketVirtual']
        if virt:
            target = virt[-1]
    if target is None:
        return False

    tree.links.new(from_socket, target)

    return any(l.from_node.as_pointer() == from_socket.node.as_pointer()
               and l.to_node.as_pointer() == out_node.as_pointer()
               for l in tree.links)


def add_interface_float(tree, name, default, parent=None):
    """Add a 0-1 Float input to the group (skips if it already exists)."""
    if interface_has_input(tree, name):
        return
    try:
        if hasattr(tree, "interface"):
            kw = {"parent": parent} if parent is not None else {}
            item = tree.interface.new_socket(name=name, in_out='INPUT',
                                             socket_type='NodeSocketFloat', **kw)
        else:
            item = tree.inputs.new('NodeSocketFloat', name)
        item.min_value = 0.0
        item.max_value = 1.0
        item.default_value = default
        try:
            item.subtype = 'FACTOR'
        except Exception:
            pass
    except Exception as e:
        print("ColorRamp Mask : could not create float input", name, e)


def find_group_user(tree, context):
    """Find (material, group node) that uses this node group."""
    cands = []
    sp_id = getattr(context.space_data, "id", None)
    if isinstance(sp_id, bpy.types.Material):
        cands.append(sp_id)
    cands.extend(bpy.data.materials)
    for mat in cands:
        if mat.node_tree is None:
            continue
        group_nodes = [n for n in mat.node_tree.nodes
                       if n.type == 'GROUP' and same_tree(n.node_tree, tree)]
        if group_nodes:
            active = mat.node_tree.nodes.active
            node = active if active in group_nodes else group_nodes[0]
            return mat, node
    return None, None


def add_pos_driver(tree, ramp_node, element_index, material, group_node, input_name):
    """Driver (Sum Values) on a ramp element position <- material group node input."""
    path = f'nodes["{ramp_node.name}"].color_ramp.elements[{element_index}].position'
    fc = tree.driver_add(path)
    for m in list(fc.modifiers):
        fc.modifiers.remove(m)
    drv = fc.driver
    drv.type = 'SUM'
    var = drv.variables.new()
    var.name = "pos"
    var.type = 'SINGLE_PROP'
    tgt = var.targets[0]
    tgt.id_type = 'MATERIAL'
    tgt.id = material
    tgt.data_path = f'node_tree.nodes["{group_node.name}"].inputs["{input_name}"].default_value'


def refresh_interface(tree):
    try:
        if hasattr(tree, "interface_update"):
            tree.interface_update(bpy.context)
    except Exception:
        pass


def get_group_input_node(tree, needed_names):
    """Return a Group Input node that has all needed outputs."""
    for n in tree.nodes:
        if n.type == 'GROUP_INPUT' and all(n.outputs.get(nm) for nm in needed_names):
            return n
    n = tree.nodes.new('NodeGroupInput')
    n.location = (-300, 0)
    return n


def set_mask_ramp(node, pos_a, pos_b, interpolation):
    """Black at pos_a, white at pos_b."""
    cr = node.color_ramp
    cr.interpolation = interpolation
    els = cr.elements
    els[1].position = pos_b
    els[0].position = pos_a
    els[0].color = (0.0, 0.0, 0.0, 1.0)
    els[1].color = (1.0, 1.0, 1.0, 1.0)


# ----------------------------------------------------------------------------
# operator
# ----------------------------------------------------------------------------

def group_ready(tree):
    """True when inside a Node Group that already has a Group Output."""
    return (tree is not None and is_node_group(tree)
            and any(n.type == 'GROUP_OUTPUT' for n in tree.nodes))


def show_error_popup(context, title, lines):
    def draw(self, ctx):
        for line in lines:
            self.layout.label(text=line)
    context.window_manager.popup_menu(draw, title=title, icon='CANCEL')


class NODE_OT_mask_mix_build(bpy.types.Operator):
    bl_idname = "node.mask_mix_build"
    bl_label = "Build Masks + Mix from Color Ramp"
    bl_description = ("Takes the active (colored) Color Ramp and creates one black/white "
                      "mask ramp per color stop, chained with Mix Color nodes whose colors "
                      "come from Group Input")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        sp = context.space_data
        return (sp and sp.type == 'NODE_EDITOR'
                and sp.tree_type == 'ShaderNodeTree'
                and sp.edit_tree is not None)

    def execute(self, context):
        props = context.scene.mask_mix_props
        tree = context.space_data.edit_tree

        # --- must be inside a Node Group that already has a Group Output
        if not group_ready(tree):
            msg = "X ERROR: No Group Output found"
            self.report({'ERROR'}, msg + ". Group your nodes first with Ctrl+G, then run the addon.")
            show_error_popup(context, msg, [
                "Make sure to Group before clicking the addon:",
                "1. Select all nodes and press Ctrl+G",
                "2. Press Tab to open the group",
                "3. Click your Color Ramp, then run the addon",
            ])
            return {'CANCELLED'}

        # --- find source ramp
        src = tree.nodes.active
        if src is None or src.type != 'VALTORGB':
            sel = [n for n in tree.nodes if n.select and n.type == 'VALTORGB']
            src = sel[0] if sel else None
        if src is None:
            self.report({'WARNING'}, "Select (make active) the colored Color Ramp node first")
            return {'CANCELLED'}

        els = list(src.color_ramp.elements)
        if len(els) < 2:
            self.report({'WARNING'}, "Color Ramp needs at least 2 color stops")
            return {'CANCELLED'}

        positions = [e.position for e in els]
        colors = [tuple(e.color) for e in els]
        n_colors = len(els)
        n_masks = n_colors - 1
        interp = src.color_ramp.interpolation
        mask_interp = interp if interp in {'LINEAR', 'EASE', 'CONSTANT'} else 'LINEAR'

        # --- factor that drove the source ramp
        fac_in = src.inputs['Fac']
        fac_from = fac_in.links[0].from_socket if fac_in.links else None

        # --- group input colors
        prefix = props.color_prefix
        names = [f"{prefix} {i + 1}" for i in range(n_colors)]
        # black/white stop range of every mask (index 1..n_masks)
        ranges = {}
        for i_m in range(1, n_colors):
            if mask_interp == 'CONSTANT':
                ra, rb = 0.0, positions[i_m]
            else:
                ra, rb = positions[i_m - 1], positions[i_m]
            if rb <= ra:
                rb = min(ra + 0.001, 1.0)
                ra = min(ra, rb - 0.001)
            ranges[i_m] = (ra, rb)
        black_names = {i_m: f"{props.pos_prefix} {i_m} Black" for i_m in ranges}
        white_names = {i_m: f"{props.pos_prefix} {i_m} White" for i_m in ranges}
        use_drivers = props.use_drivers and is_node_group(tree)
        drv_mat, drv_gn = (None, None)
        if use_drivers:
            drv_mat, drv_gn = find_group_user(tree, context)
            if drv_mat is None:
                self.report({'WARNING'}, "Drivers skipped: this node group is not used in any material")
                use_drivers = False

        group_node = None
        if props.use_group_input:
            # order: Color 1, then for each mask: Color n, Pos n Black, Pos n White
            add_interface_color(tree, names[0], colors[0])
            for i_m in range(1, n_colors):
                panel = None
                if props.use_panels:
                    panel = get_or_create_panel(tree, f"{names[i_m]} Mask")
                add_interface_color(tree, names[i_m], colors[i_m], parent=panel)
                if use_drivers:
                    if mask_interp != 'CONSTANT':
                        add_interface_float(tree, black_names[i_m], ranges[i_m][0], parent=panel)
                    add_interface_float(tree, white_names[i_m], ranges[i_m][1], parent=panel)
            refresh_interface(tree)
            for nm, col in zip(names, colors):
                push_color_defaults(tree, nm, col)
            if use_drivers:
                for i_m in range(1, n_colors):
                    if mask_interp != 'CONSTANT':
                        push_color_defaults(tree, black_names[i_m], ranges[i_m][0])
                    push_color_defaults(tree, white_names[i_m], ranges[i_m][1])
            group_node = get_group_input_node(tree, names)
        else:
            use_drivers = False

        def color_socket(i, x, y):
            if group_node is not None:
                s = group_node.outputs.get(names[i])
                if s is not None:
                    return s
            rgb = tree.nodes.new('ShaderNodeRGB')
            rgb.label = names[i]
            rgb.location = (x, y)
            rgb.outputs[0].default_value = colors[i]
            return rgb.outputs[0]

        # --- build
        x0 = src.location.x + src.width + 80
        y0 = src.location.y + props.y_offset  # start just above the source ramp
        step_y = 260

        n_drivers = 0
        driver_errors = []
        prev = color_socket(0, x0 + 300, y0 - 120)
        last_result = prev

        for i in range(1, n_colors):
            # mask ramp
            mask = tree.nodes.new('ShaderNodeValToRGB')
            mask.label = f"Mask {i}"
            mask.width = src.width
            mask.location = (x0, y0 + (i - 1) * step_y)  # stack upward

            a, b = ranges[i]
            set_mask_ramp(mask, a, b, mask_interp)

            if use_drivers:
                jobs = [(1, white_names[i])]
                if mask_interp != 'CONSTANT':
                    jobs.insert(0, (0, black_names[i]))
                for el_idx, in_name in jobs:
                    try:
                        add_pos_driver(tree, mask, el_idx, drv_mat, drv_gn, in_name)
                        n_drivers += 1
                    except Exception as e:
                        driver_errors.append(f"{mask.name}[{el_idx}]: {e}")

            if fac_from is not None:
                tree.links.new(fac_from, mask.inputs['Fac'])

            # mix node
            mix = tree.nodes.new('ShaderNodeMix')
            mix.data_type = 'RGBA'
            mix.blend_type = 'MIX'
            mix.clamp_factor = True
            mix.label = f"Mix {i}"
            mix.location = (x0 + src.width + 100 + (i - 1) * 190,
                            y0 + (i - 1) * step_y)

            # fallback: even if a link is missing, A/B show the ramp's colors (not white)
            find_socket(mix.inputs, 'A_Color').default_value = colors[i - 1]
            find_socket(mix.inputs, 'B_Color').default_value = colors[i]

            tree.links.new(mask.outputs['Color'], find_socket(mix.inputs, 'Factor_Float'))
            tree.links.new(last_result, find_socket(mix.inputs, 'A_Color'))
            tree.links.new(color_socket(i, mix.location.x - 160, mix.location.y - 120),
                           find_socket(mix.inputs, 'B_Color'))
            last_result = find_socket(mix.outputs, 'Result_Color')

        # --- hook up the result where the source ramp was going
        if props.reroute_output:
            targets = [l.to_socket for l in src.outputs['Color'].links]
            for t in targets:
                tree.links.new(last_result, t)

        out_msg = ""
        if props.ensure_output and is_node_group(tree):
            last_x = x0 + src.width + 100 + n_masks * 190
            try:
                if ensure_group_output(tree, last_result, last_x, y0):
                    out_msg = ", linked to Group Output"
                else:
                    self.report({'WARNING'}, "Could not connect to Group Output")
            except Exception as e:
                self.report({'WARNING'}, f"Group Output failed: {e}")
        elif props.ensure_output:
            out_msg = " (not inside a Node Group: Group Output skipped)"

        if driver_errors:
            self.report({'WARNING'}, f"{len(driver_errors)} driver(s) failed: {driver_errors[0]}")

        if props.remove_source:
            tree.nodes.remove(src)

        if props.use_drivers and not use_drivers:
            drv_msg = ", no drivers (see warning)" if is_node_group(tree) else ", no drivers (open the group, not the material)"
        else:
            drv_msg = f", {n_drivers} driver(s)"
        self.report({'INFO'}, f"Created {n_masks} mask(s) and Mix node(s){drv_msg}{out_msg}")
        return {'FINISHED'}


class NODE_OT_mask_mix_connect_output(bpy.types.Operator):
    bl_idname = "node.mask_mix_connect_output"
    bl_label = "Connect Active Node to Group Output"
    bl_description = ("Link the active node's color result (e.g. your last Mix) to the "
                      "Group Output. Creates the Group Output / Result socket if missing")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        sp = context.space_data
        return sp and sp.type == 'NODE_EDITOR' and sp.edit_tree is not None

    def execute(self, context):
        tree = context.space_data.edit_tree
        node = tree.nodes.active
        if node is None or not node.outputs:
            self.report({'WARNING'}, "Make the last Mix node the active node first")
            return {'CANCELLED'}
        out = find_socket(node.outputs, 'Result_Color')
        if out is None:
            out = next((o for o in node.outputs if o.type == 'RGBA' and o.enabled), None)
        if out is None:
            out = next((o for o in node.outputs if o.enabled), None)
        if out is None:
            self.report({'WARNING'}, "Active node has no output socket")
            return {'CANCELLED'}
        if tree.is_embedded_data if hasattr(tree, "is_embedded_data") else False:
            self.report({'WARNING'}, "Open the Node Group (Tab into it) - this is a material tree")
            return {'CANCELLED'}
        ok = ensure_group_output(tree, out, node.location.x + 300, node.location.y)
        if ok:
            self.report({'INFO'}, f"{node.name} connected to Group Output")
            return {'FINISHED'}
        self.report({'ERROR'}, "Could not connect to Group Output")
        return {'CANCELLED'}


class NODE_OT_mask_mix_move_material_output(bpy.types.Operator):
    bl_idname = "node.mask_mix_move_material_output"
    bl_label = "Move Material Output Outside Group"
    bl_description = ("Open the node group first. Exposes what the Material Output inside the group "
                      "receives (e.g. BSDF) as a Group Output, deletes the inner Material Output "
                      "and connects the group node to the Material Output in the material")
    bl_options = {'REGISTER', 'UNDO'}

    TYPE_MAP = {'SHADER': 'NodeSocketShader', 'VECTOR': 'NodeSocketVector', 'VALUE': 'NodeSocketFloat',
                'RGBA': 'NodeSocketColor'}

    @classmethod
    def poll(cls, context):
        sp = context.space_data
        return sp and sp.type == 'NODE_EDITOR' and sp.edit_tree is not None

    def execute(self, context):
        tree = context.space_data.edit_tree
        if not is_node_group(tree):
            self.report({'WARNING'}, "Open the Node Group first (select it and press Tab)")
            return {'CANCELLED'}

        mo_nodes = [n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL']
        if not mo_nodes:
            self.report({'WARNING'}, "No Material Output inside this group")
            return {'CANCELLED'}
        mo = next((n for n in mo_nodes if getattr(n, "is_active_output", False)), mo_nodes[0])

        mat, gn = find_group_user(tree, context)
        if mat is None:
            self.report({'WARNING'}, "This group is not used in a material, nothing changed")
            return {'CANCELLED'}

        items = []
        for sk in mo.inputs:
            if sk.is_linked and sk.type in self.TYPE_MAP:
                items.append((sk.name, sk.type, sk.links[0].from_socket))
        if not items:
            self.report({'WARNING'}, "The Material Output has nothing connected to it")
            return {'CANCELLED'}

        outs = [n for n in tree.nodes if n.type == 'GROUP_OUTPUT']
        out_node = next((n for n in outs if getattr(n, "is_active_output", False)),
                        outs[0] if outs else None)
        if out_node is None:
            out_node = tree.nodes.new('NodeGroupOutput')
            out_node.location = (mo.location.x + 250, mo.location.y)

        exposed = {}
        for in_name, in_type, from_sock in items:
            # reuse an existing Group Output socket already fed by this socket
            existing = None
            for l in tree.links:
                if (l.from_socket.as_pointer() == from_sock.as_pointer()
                        and l.to_node.as_pointer() == out_node.as_pointer()):
                    existing = l.to_socket
                    break
            if existing is not None:
                exposed[in_name] = existing.name
                continue
            if hasattr(tree, "interface"):
                tree.interface.new_socket(name=in_name, in_out='OUTPUT',
                                          socket_type=self.TYPE_MAP[in_type])
            else:
                tree.outputs.new(self.TYPE_MAP[in_type], in_name)
            refresh_interface(tree)
            target = next((sk for sk in real_inputs(out_node) if sk.name == in_name), None)
            if target is None:
                target = next((sk for sk in out_node.inputs if sk.bl_idname == 'NodeSocketVirtual'), None)
            if target is None:
                self.report({'WARNING'}, f"Could not expose {in_name}")
                continue
            tree.links.new(from_sock, target)
            exposed[in_name] = in_name

        if not exposed:
            return {'CANCELLED'}

        # remove the Material Output(s) inside the group
        for n in mo_nodes:
            tree.nodes.remove(n)

        # material side: find / create Material Output and connect
        mtree = mat.node_tree
        mouts = [n for n in mtree.nodes if n.type == 'OUTPUT_MATERIAL']
        m_out = next((n for n in mouts if getattr(n, "is_active_output", False)),
                     mouts[0] if mouts else None)
        if m_out is None:
            m_out = mtree.nodes.new('ShaderNodeOutputMaterial')
            m_out.location = (gn.location.x + 300, gn.location.y)

        done = 0
        for in_name, sock_name in exposed.items():
            gsock = gn.outputs.get(sock_name)
            msock = m_out.inputs.get(in_name)
            if gsock is not None and msock is not None:
                mtree.links.new(gsock, msock)
                done += 1
        self.report({'INFO'}, f"Material Output moved outside the group ({done} link(s) made in {mat.name})")
        return {'FINISHED'}


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------

class MaskMixProps(bpy.types.PropertyGroup):
    use_group_input: bpy.props.BoolProperty(
        name="Use Group Input",
        description="Create Color sockets on the node group (off = use RGB nodes)",
        default=True,
    )
    color_prefix: bpy.props.StringProperty(
        name="Color Name",
        description="Prefix for the color sockets",
        default="Color",
    )
    y_offset: bpy.props.FloatProperty(
        name="Height Offset",
        description="How far above the source ramp the first mask is placed (stacks upward from there)",
        default=60.0,
    )
    reroute_output: bpy.props.BoolProperty(
        name="Connect Result Where Ramp Went",
        description="Link the final Mix result to whatever the source ramp's Color output was connected to",
        default=True,
    )
    use_panels: bpy.props.BoolProperty(
        name="Collapsible Panels",
        description="Put each color and its Black/White sliders in its own collapsible panel on the group node",
        default=False,
    )
    use_drivers: bpy.props.BoolProperty(
        name="Add Pos Drivers",
        description=("Create Float inputs on the group (one per stop position) and drive each mask "
                     "ramp position from the material's group node (Sum Values)"),
        default=True,
    )
    pos_prefix: bpy.props.StringProperty(
        name="Pos Name",
        description="Prefix for the position sockets",
        default="Pos",
    )
    ensure_output: bpy.props.BoolProperty(
        name="Ensure Group Output",
        description="Connect the final result to the Group Output (creates the node / a Result socket if missing)",
        default=True,
    )
    remove_source: bpy.props.BoolProperty(
        name="Delete Source Ramp",
        description="Remove the original colored ramp afterwards",
        default=False,
    )


class NODE_PT_mask_mix(bpy.types.Panel):
    bl_label = "ColorRamp Mask v1.0"
    bl_idname = "NODE_PT_ColorRamp_Mask"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "ColorRamp Mask"

    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == 'ShaderNodeTree'

    def draw(self, context):
        layout = self.layout
        props = context.scene.mask_mix_props
        layout.label(text="Click your colored Color Ramp, then:")
        layout.prop(props, "use_group_input")
        layout.prop(props, "color_prefix")
        layout.prop(props, "y_offset")
        layout.prop(props, "reroute_output")
        layout.prop(props, "use_panels")
        layout.prop(props, "use_drivers")
        if props.use_drivers:
            layout.prop(props, "pos_prefix")
        layout.prop(props, "ensure_output")
        layout.prop(props, "remove_source")
        tree = context.space_data.edit_tree
        box = layout.box()
        if group_ready(tree):
            box.label(text="Group + Group Output found", icon='CHECKMARK')
        else:
            box.alert = True
            box.label(text="X ERROR: No Group Output found", icon='ERROR')
            box.label(text="Group first: select all, Ctrl+G, Tab")
        box.label(text="Reminder: Ctrl+G before clicking!", icon='INFO')
        layout.operator("node.mask_mix_build", icon='NODE_COMPOSITING')
        layout.separator()
        layout.label(text="Select your last Mix node, then:")
        layout.operator("node.mask_mix_connect_output", icon='OUTPUT')
        layout.separator()
        layout.label(text="Inside the group:")
        layout.operator("node.mask_mix_move_material_output", icon='EXPORT')


classes = (MaskMixProps, NODE_OT_mask_mix_build, NODE_OT_mask_mix_connect_output, NODE_OT_mask_mix_move_material_output, NODE_PT_mask_mix)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.mask_mix_props = bpy.props.PointerProperty(type=MaskMixProps)


def unregister():
    del bpy.types.Scene.mask_mix_props
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
