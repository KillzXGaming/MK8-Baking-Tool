from enum import Enum
import bpy, os
import subprocess
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper
from bpy.props import *

bl_info = {
    "name": "Bake Tool",
    "description": "Addon to assist with baking gsys type bake maps.",
    "author": "KillzXGaming",
    "version": (2, 0),
    "blender": (3, 0, 0),
    "location": "View3D",
    "category": "3D View",
}

class UnwrapMeshGroup(bpy.types.Operator):
    bl_idname = 'bake_tools.unwrap_meshes_group'
    bl_label = 'Unwrap Mesh Group'
    bl_description = 'Unwrap Mesh Group'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return {'FINISHED'}

def SetupBakeSettings(context):
    ## bake settings
    settings = context.scene.bake_settings
    ## Render using cycles in gpu mode
    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.cycles.device = 'GPU'
    ##Prepare bake quality
    if (settings.bake_quality == '1'):
        bpy.context.scene.cycles.samples = 1
    if (settings.bake_quality == '2'):
        bpy.context.scene.cycles.samples = 128
    if (settings.bake_quality == '3'):
        bpy.context.scene.cycles.samples = 1024
    if (settings.bake_quality == '4'):
        bpy.context.scene.cycles.samples = 4096

    print('Cycles sample amount ' + str(bpy.context.scene.cycles.samples))
    bpy.data.worlds["World"].cycles_visibility.diffuse = False
    bpy.data.worlds["World"].node_tree.nodes["Background"].inputs[1].default_value = 0.0

def TryUnwrapMeshes(context, uvlayer):
    """ Unwraps selected meshes if a given UV layer does not exist, or if `settings.force_unwrap = True`"""

    ## Multi unwrap selected
    bpy.ops.object.editmode_toggle()
    bpy.ops.mesh.select_all(action='SELECT') # for all faces

    for obj in bpy.context.selected_objects:
        bake_layer =  obj.data.uv_layers.get(uvlayer)

        is_new = False
        if not bake_layer:
            bake_layer = obj.data.uv_layers.new(name=uvlayer)
            is_new = True
        if is_new or context.scene.bake_settings.force_unwrap:
            ##set active to unwrap
            bake_layer.active = True

            # Make sure island margin matches the set bake margin (or else individual mesh
            #   bake margins will overwrite previous bake results)
            bpy.ops.uv.smart_project(island_margin=context.scene.render.bake.margin)

    bpy.ops.object.editmode_toggle()

def BeginMeshBake(img, obj, uvlayer):
    ## Bake via a selected shader node
    for mat in obj.data.materials:

        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        texture_node =nodes.new('ShaderNodeTexImage')
        texture_node.name = 'Bake_node'
        texture_node.select = True
        nodes.active = texture_node
        texture_node.image = img #Assign the image to the node

def EndMeshBake(obj):
    #In the last step, we are going to delete the nodes we created earlier
    for mat in obj.data.materials:
        for n in mat.node_tree.nodes:
            if n.name.startswith("Bake_node"):
                mat.node_tree.nodes.remove(n)

class BakeLightmapOp(bpy.types.Operator):
    bl_idname = 'bake_tools.bake_lightmap_op'
    bl_label = 'Bake Lightmap'
    bl_description = 'Bake Lightmap'
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    img_lightmap = None

    def modal(self, context, event):
        ## bake settings
        settings = context.scene.bake_settings

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self.cancel(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            ## Finished baking ao. Do shadows next
            if self.img_lightmap.is_dirty:
                save_light_map(context)
                self.finish(context)
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def execute(self, context):
        ## bake settings
        settings = context.scene.bake_settings

        SetupBakeSettings(context)

        uvlayer = 'Bake'

        ## Output name
        image_name = settings.bake_name + '_b01'

        ## Remove any existing bakes
        for image in bpy.data.images:
            if image.name == image_name:
                bpy.data.images.remove(image)

        ## Target bake map
        img = bpy.data.images.new(image_name,settings.image_size,settings.image_size)

        TryUnwrapMeshes(context, uvlayer)

        for obj in bpy.context.selected_objects:
            if (obj is None or obj.data.materials is None):
                continue

            BeginMeshBake(img, obj,uvlayer)
            bpy.context.view_layer.objects.active = obj


        result = bpy.ops.object.bake('INVOKE_DEFAULT', type='DIFFUSE', pass_filter={'DIRECT','INDIRECT'},uv_layer=uvlayer, save_mode='EXTERNAL')
        if result != {'RUNNING_MODAL'}:
            self.report({'WARNING'}, "Failed to start baking")
            self.end_meshes(context)
            return {'FINISHED'}



        return {'RUNNING_MODAL'}

    def save_light_map(self,context):
        ## bake settings
        settings = context.scene.bake_settings
        folder =  bpy.path.abspath(settings.export_path)

        img.save_render(filepath=folder + '\\' + image_name + '.exr')

        #Set active image as new bake
        for area in bpy.context.screen.areas :
            if area.type == 'IMAGE_EDITOR' :
                    area.spaces.active.image = img

    def cancel(self, context):
        self.report({'INFO'}, "Baking map cancelled")
        self.end_meshes(context)

    def finish(self, context):
        wm = context.window_manager
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        self.report({'INFO'}, "Baking map completed")
        self.end_meshes(context)

    def end_meshes(self, context):
        for obj in bpy.context.selected_objects:
            if (obj is None or obj.data.materials is None):
                continue

            ## Remove render node
            EndMeshBake(obj)

class BakeShadowsOp(bpy.types.Operator):
    bl_idname = 'bake_tools.bake_shadow_op'
    bl_label = 'Bake Shadows'
    bl_description = 'Bake Shadows'
    bl_options = {'REGISTER', 'UNDO'}

    UV_LAYER = 'Bake'

    def execute(self, context):
        ## bake settings
        settings = context.scene.bake_settings

        SetupBakeSettings(context)

        ## Output name
        image_name = settings.bake_name
        ## Remove any existing bakes
        for image in bpy.data.images:
            if image.name in [image_name + "_ao", image_name + "_shadows"]:
                bpy.data.images.remove(image)

        # Remove selected non-meshes (E.g. lights, curves, etc.) from selection
        mesh_obj = None
        for obj in bpy.context.selected_objects:
            if obj.type != 'MESH':
                # Can only bake meshes
                obj.select_set(False)
                continue
            mesh_obj = obj

        # Sanity check: should have at least one mesh to bake
        if mesh_obj is None:
            self.report({'WARNING'}, "No meshes selected")
            return {'FINISHED'}

        # Make sure a mesh object is the active one (we don't care which one specifically)
        bpy.context.view_layer.objects.active = mesh_obj

        ## Try to unwrap meshes
        TryUnwrapMeshes(context, self.UV_LAYER)

        ## Target bake maps
        if settings.bake_ao:
            img_ao = bpy.data.images.new(image_name + "_ao", settings.image_size, settings.image_size)
            # TODO: AO Baking; there's not actually a way to guarantee that this is finished before
            #   shadow baking starts, so this setup will need to be modified further.

        if settings.bake_shadows:
            img_shadow = bpy.data.images.new(image_name + "_shadows", settings.image_size, settings.image_size)
            self.bake_shadow_map(context, img_shadow)

        return {'FINISHED'}

    def bake_shadow_map(self, context, image):
        print("Baking SHADOWS")
        uvlayer = 'Bake'
        ## bake settings
        settings = context.scene.bake_settings
        ##Prepare shader nodes for meshes
        for obj in bpy.context.selected_objects:
            if (obj is None or obj.data.materials is None):
                continue

            ## Select node to bake
            BeginMeshBake(image, obj, uvlayer)
            bpy.context.view_layer.objects.active = obj

        if (settings.bake_shadows):
            result = bpy.ops.object.bake('INVOKE_DEFAULT', type='SHADOW', uv_layer=uvlayer, save_mode='EXTERNAL')
            if result != {'RUNNING_MODAL'}:
                self.report({'WARNING'}, "Failed to start baking")
                return {'FINISHED'}

        return {'RUNNING_MODAL'}

class ImageChannels(Enum):
    RED = 0
    GREEN = 1
    BLUE = 2
    ALPHA = 3

class CombineShadowsOp(bpy.types.Operator):
    """
        Combine AO and shadow bakes into the correct channels of a single
        image and clean up nodes that were created during the baking process.
    """
    bl_idname = 'bake_tools.combine_shadows_op'
    bl_label = 'Combine Bakes'
    bl_description = 'Combines Ambient Occlusion and Shadow bakes into specific channels of a single image as expected by MK8'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ## bake settings
        settings = context.scene.bake_settings

        ## Output name
        combined_img_name = settings.bake_name + '_combined'
        ## Remove any existing bakes
        for image in bpy.data.images:
            if image.name == combined_img_name:
                bpy.data.images.remove(image)
                break

        combined_img = bpy.data.images.new(combined_img_name, settings.image_size, settings.image_size)

        # Inject AO
        if settings.bake_ao:
            ao_img_name = settings.bake_name + "_ao"
            if ao_img_name not in bpy.data.images:
                self.report({'WARNING'}, "AO Map not found")
                return {'FINISHED'}
            self.inject_into_channel(bpy.data.images[ao_img_name], combined_img, ImageChannels.RED)

        # Inject Shadows
        if settings.bake_shadows:
            shadows_img_name = settings.bake_name + "_shadows"
            if shadows_img_name not in bpy.data.images:
                self.report({'WARNING'}, "Shadow Map not found")
                return {'FINISHED'}
            self.inject_into_channel(bpy.data.images[shadows_img_name], combined_img, ImageChannels.GREEN)

        # Set combined image as active
        for area in bpy.context.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                area.spaces.active.image = combined_img

        # Cleanup old nodes
        self.cleanup_bake_nodes(context)

        return {'FINISHED'}

    def inject_into_channel(self, from_img: bpy.types.Image, to_img: bpy.types.Image, channel: ImageChannels):
        """ Injects an image into one of the (RGBA) channels of another image """
        # Image editing is slow, so we create a copy of all the pixels first: https://blender.stackexchange.com/a/3678
        #   Using the tuple object is way faster than direct access to Image.pixels
        from_pixels = from_img.pixels[:]
        to_pixels = list(to_img.pixels)

        # Sanity check
        assert len(from_pixels) == len(to_pixels)

        # Modify the copied pixels based on the pixels of `from_img`
        #   Channel 0, 1, 2, 3 relate to R, G, B, A
        for i in range(channel.value, len(from_pixels), 4):
            to_pixels[i] = from_pixels[i]

        # Write copied pixels back to image (Slice notation here means to replace in-place)
        to_img.pixels[:] = to_pixels
        to_img.update()

    def cleanup_bake_nodes(self, context):
        """ Deletes nodes created during the baking process """
        for obj in bpy.context.selected_objects:
            if (obj is None or obj.data.materials is None):
                continue

            ## Remove render node
            EndMeshBake(obj)


class BakeSettings(bpy.types.PropertyGroup):

    ############################
    ## Standard Settings
    force_unwrap: bpy.props.BoolProperty(
        name="Force Unwrap",
        description="Forces to unwrap selected before baking",
        default = False)

    bake_name : StringProperty(
            name="Texture Name",
            default="Gu_Map_g00")

    export_path : StringProperty(
            name="Export Path",
            default = "C:\\TEMP",
            description="Choose a directory:",
            maxlen=1024,
            subtype='DIR_PATH')

    image_size: bpy.props.IntProperty(
        name="Size",
        description="Size of texture for baking",
        default = 1024)

    bake_group: bpy.props.IntProperty(
        name="Group Index",
        description="Group to bake texture to",
        default = 0)

    bake_quality: EnumProperty(
        name="Quality",
        items=(
               ('1', 'Preview', 'Has artifacts. Use to quickly preview'),
               ('2', 'Medium', 'Decent balance with quality and speed'),
               ('3', 'High', 'Great quality and not too slow'),
               ('4', 'Ultra', 'Best quality but very slow'),
            ),
        default='2')

    ############################
    ## Shadow Settings
    bake_ao: bpy.props.BoolProperty(
        name="Bake Ambient Occlusion",
        description="Bake Ambient Occlusion",
        default = True)

    bake_shadows: bpy.props.BoolProperty(
        name="Bake Shadows",
        description="Bake Shadows",
        default = True)

    shadow_type: bpy.props.EnumProperty(
        name="Type",
        items=(
               ('1', 'AO', ''),
               ('2', 'AO + Shadow (RG)', 'Bakes AO to red channel. shadows to green channel.'),
            ),
        default='2')

    ############################
    ## Lightmap Settings
    lightmap_format: EnumProperty(
        name="Format",
        items=(
               ('1', 'Png', 'No Encoding'),
               ('2', 'Exr (HDR)', 'HDR Encoding (stores HDR to alpha channel for import)'),
            ),
        default='2')

    ############################
    ## BGENV Settings
    directional_light: FloatVectorProperty(
        name = "Light Direction",
        description="Light Direction",
        default=(0.0, -1.0, 0.0),
        min= 0.0,
        max = 0.1,
        subtype = 'XYZ'
    )

class BgenvSettings(bpy.types.Panel):
    bl_label = "BGENV Settings"
    bl_category = "Bake Editor"
    bl_idname = "VIEW3D_PT_bgenv"
    bl_space_type = 'VIEW_3D'
    bl_parent_id = "VIEW3D_PT_bake"
    bl_region_type = 'UI'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.bake_settings
        col = layout.column()

        obj = bpy.data.objects["Sun"]
        if (obj is not None):
            axis_angle = obj.rotation_axis_angle

           # layout.label(text=str(axis_angle[0]))
           # layout.label(text=str(axis_angle[1]))
         #   layout.label(text=str(axis_angle[2]))

           # settings.directional_light = (matXR, matYR, matZR)

      #  layout.prop(settings, "directional_light")

class ShadowToolPanel(bpy.types.Panel):
    bl_label = "Shadow Settings"
    bl_category = "Bake Editor"
    bl_idname = "VIEW3D_PT_shadow"
    bl_space_type = 'VIEW_3D'
    bl_parent_id = "VIEW3D_PT_bake"
    bl_region_type = 'UI'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.bake_settings
        col = layout.column()

        scene = context.scene

        layout.prop(settings, "shadow_type")
        layout.prop(settings, "bake_ao")

        if (settings.shadow_type == "2"):
            layout.prop(settings, "bake_shadows")

        layout.operator("bake_tools.bake_shadow_op")

        layout.operator("bake_tools.combine_shadows_op")

class LightmapToolPanel(bpy.types.Panel):
    bl_label = "Lightmap Settings"
    bl_category = "Bake Editor"
    bl_idname = "VIEW3D_PT_lightmap"
    bl_space_type = 'VIEW_3D'
    bl_parent_id = "VIEW3D_PT_bake"
    bl_region_type = 'UI'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.bake_settings

        scene = context.scene

        layout.prop(settings, "lightmap_format")

        layout.operator("bake_tools.bake_lightmap_op")

class BakeToolPanel(bpy.types.Panel):
    bl_label = "Bake Editor"
    bl_category = "Bake Editor"
    bl_idname = "VIEW3D_PT_bake"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.bake_settings

        scene = context.scene

        layout.prop(settings, "export_path")

        layout.prop(settings, "bake_name")

        layout.prop(settings, "bake_quality")

        # In Cycles, margins are applied per mesh instead of at the very end. This means that it's
        #   possible they overwrite previous bake results. To prevent that, a low (or zero) margin
        #   should be used, or UV islands should be packed with a margin too.
        #   See: https://developer.blender.org/T83971
        layout.prop(bpy.context.scene.render.bake, "margin")

        # layout.prop(settings, "bake_group")

        layout.prop(settings, "image_size")

        layout.prop(settings, "force_unwrap")

def register():

    bpy.utils.register_class(BakeToolPanel)
    bpy.utils.register_class(ShadowToolPanel)
    bpy.utils.register_class(LightmapToolPanel)
    # bpy.utils.register_class(BgenvSettings)
    bpy.utils.register_class(BakeSettings)
    bpy.utils.register_class(BakeShadowsOp)
    bpy.utils.register_class(CombineShadowsOp)
    bpy.utils.register_class(BakeLightmapOp)
    bpy.utils.register_class(UnwrapMeshGroup)

    bpy.types.Scene.bake_settings = bpy.props.PointerProperty(type=BakeSettings)

def unregister():
    bpy.utils.unregister_class(BakeToolPanel)
    bpy.utils.unregister_class(ShadowToolPanel)
    bpy.utils.unregister_class(LightmapToolPanel)
    # bpy.utils.unregister_class(BgenvSettings)
    bpy.utils.unregister_class(BakeSettings)
    bpy.utils.unregister_class(BakeShadowsOp)
    bpy.utils.unregister_class(CombineShadowsOp)
    bpy.utils.unregister_class(BakeLightmapOp)
    bpy.utils.unregister_class(UnwrapMeshGroup)

    del bpy.types.Scene.bake_settings

if __name__ == "__main__":
    register()
