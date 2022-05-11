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

def TryUnwrapMeshes(settings, uvlayer):

    ## Multi unwrap selected
    bpy.ops.object.editmode_toggle()
    bpy.ops.mesh.select_all(action='SELECT') # for all faces

    for obj in bpy.context.selected_objects:
         ##set object as active
         bpy.context.view_layer.objects.active = obj

    for obj in bpy.context.selected_objects:
        bake_layer =  obj.data.uv_layers.get(uvlayer)

        is_new = False
        if not bake_layer:
            bake_layer = obj.data.uv_layers.new(name=uvlayer)
            is_new = True
        if is_new or settings.force_unwrap:
            ##set active to unwrap
            bake_layer.active = True

            bpy.ops.uv.smart_project()

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
            if n.name == 'Bake_node':
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

        TryUnwrapMeshes(settings, uvlayer)

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

    _timer = None
    _timer2 = None

    img_ao = None
    img_shadow = None
    baked_shadows = False
    baked_ao = False
    is_baking = False

    def modal(self, context, event):
        ## bake settings
        settings = context.scene.bake_settings

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self.cancel(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            ## Finished baking ao. Do shadows next
            if settings.bake_ao and self.img_ao.is_dirty and self.baked_ao == False:
                self.baked_ao = True
                self.finish(context)
                self.bake_shadow_map(context)

                return {'PASS_THROUGH'}

            ## Finished baking shadows. Finalize the output
            if settings.bake_shadows and self.img_shadow.is_dirty and self.baked_shadows == False:
                self.baked_shadows = True
                self.finish(context)
                self.save_shadow_map(context)

                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def bake_ao_map(self, context):
        print("Baking AO")
        uvlayer = 'Bake'
        ## bake settings
        settings = context.scene.bake_settings
        ##Prepare shader nodes for meshes
        for obj in bpy.context.selected_objects:
            if (obj is None or obj.data.materials is None):
                continue

            ## Select node to bake
            BeginMeshBake(self.img_ao, obj,uvlayer)
            bpy.context.view_layer.objects.active = obj

        if (settings.bake_ao):
            result = bpy.ops.object.bake('INVOKE_DEFAULT', type='AO',uv_layer=uvlayer, save_mode='EXTERNAL')
            if result != {'RUNNING_MODAL'}:
                self.report({'WARNING'}, "Failed to start baking")
                self.end_meshes(context)
                return {'FINISHED'}


        return {'RUNNING_MODAL'}

    def bake_shadow_map(self, context):
        print("Baking SHADOWS")
        uvlayer = 'Bake'
        ## bake settings
        settings = context.scene.bake_settings
        ##Prepare shader nodes for meshes
        for obj in bpy.context.selected_objects:
            if (obj is None or obj.data.materials is None):
                continue

            ## Select node to bake
            BeginMeshBake(self.img_shadow, obj,uvlayer)
            bpy.context.view_layer.objects.active = obj

        if (settings.bake_shadows):
            result = bpy.ops.object.bake('INVOKE_DEFAULT', type='SHADOW',uv_layer=uvlayer, save_mode='EXTERNAL')
            if result != {'RUNNING_MODAL'}:
                self.report({'WARNING'}, "Failed to start baking")
                self.end_meshes(context)
                return {'FINISHED'}

        return {'RUNNING_MODAL'}

    def execute(self, context):
        ## bake settings
        settings = context.scene.bake_settings

        SetupBakeSettings(context)

        uvlayer = 'Bake'

        ## Output name
        image_name = settings.bake_name + '_b00'
        ## Remove any existing bakes
        for image in bpy.data.images:
            if image.name == image_name:
                bpy.data.images.remove(image)

        ## Target bake map
        self.img_ao = bpy.data.images.new(image_name,settings.image_size,settings.image_size)
        self.img_shadow = bpy.data.images.new(image_name,settings.image_size,settings.image_size)

        ## Try to unwrap meshes
        TryUnwrapMeshes(settings, uvlayer)

        self.baked_ao = False
        self.bake_shadows = False

        ## Start a timer for the baking process
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)

        ## Bake AO first
        self.bake_shadow_map(context)

        return {'RUNNING_MODAL'}

    def save_shadow_map(self,context):
        print("save_shadow_map")
        ## bake settings
        settings = context.scene.bake_settings
        ## Output name
        image_name = settings.bake_name + '_b00'

        ## Transfer the shadow image to the ao green channel
        source_pixels = self.img_shadow.pixels[:]
        target_pixels = list(self.img_ao.pixels)

        assert len(source_pixels) == len(target_pixels)

        for i in range(0, len(source_pixels), 4):
            target_pixels[i+0] = 1.0
            target_pixels[i+1] = source_pixels[i]
            target_pixels[i+2] = 0.0
            target_pixels[i+3] = 1.0

        ## Update the AO image with the shadow
        self.img_ao.pixels[:] = target_pixels
        self.img_ao.update()

        folder =  bpy.path.abspath(settings.export_path)

        ## Save to disk
        self.img_ao.save_render(filepath=folder + '\\' + image_name + '.png')

        bpy.data.images.remove(self.img_shadow)

        #Set active image as new bake
        for area in bpy.context.screen.areas :
            if area.type == 'IMAGE_EDITOR' :
                    area.spaces.active.image = self.img_ao

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

class BakeSettings(bpy.types.PropertyGroup):

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

    directional_light: FloatVectorProperty(
        name = "Light Direction",
        description="Light Direction",
        default=(0.0, -1.0, 0.0),
        min= 0.0,
        max = 0.1,
        subtype = 'XYZ'
        )

    image_size: bpy.props.IntProperty(
        name="Size",
        description="Size of texture for baking",
        default = 1024)

    bake_group: bpy.props.IntProperty(
        name="Group Index",
        description="Group to bake texture to",
        default = 0)

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

    bake_quality: EnumProperty(
        name="Quality",
        items=(
               ('1', 'Preview', 'Has artifacts. Use to quickly preview'),
               ('2', 'Medium', 'Decent balance with quality and speed'),
               ('3', 'High', 'Great quality and not too slow'),
               ('4', 'Ultra', 'Best quality but very slow'),
            ),
        default='2')

    lightmap_format: EnumProperty(
        name="Format",
        items=(
               ('1', 'Png', 'No Encoding'),
               ('2', 'Exr (HDR)', 'HDR Encoding (stores HDR to alpha channel for import)'),
            ),
        default='2')

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

        layout.prop(bpy.context.scene.render.bake, "margin")


     #   layout.prop(settings, "bake_group")

        layout.prop(settings, "image_size")

        layout.prop(settings, "force_unwrap")

def register():

    bpy.utils.register_class(BakeToolPanel)
    bpy.utils.register_class(ShadowToolPanel)
    bpy.utils.register_class(LightmapToolPanel)
  #  bpy.utils.register_class(BgenvSettings)
    bpy.utils.register_class(BakeSettings)
    bpy.utils.register_class(BakeShadowsOp)
    bpy.utils.register_class(BakeLightmapOp)
    bpy.utils.register_class(UnwrapMeshGroup)

    bpy.types.Scene.bake_settings = bpy.props.PointerProperty(type=BakeSettings)

def unregister():
    bpy.utils.unregister_class(BakeToolPanel)
    bpy.utils.unregister_class(ShadowToolPanel)
    bpy.utils.unregister_class(LightmapToolPanel)
  #  bpy.utils.unregister_class(BgenvSettings)
    bpy.utils.unregister_class(BakeSettings)
    bpy.utils.unregister_class(BakeShadowsOp)
    bpy.utils.unregister_class(BakeLightmapOp)
    bpy.utils.unregister_class(UnwrapMeshGroup)

    del bpy.types.Scene.bake_settings

if __name__ == "__main__":
    register()
