"""skippy_puppet (SPIKE): draws Skippy's face on the box from a draw list (puppet.py's scene format) instead of receiving pixel tiles.

docs/puppet-mode.md. This first version only holds one recorded scene (scene_data.h) and times how fast the box can rasterize it: `bench()` draws the whole
scene and a dirty rectangle many times, logs microseconds per op kind, and leaves the scene on the panel.
"""
import esphome.codegen as cg
from esphome.components import display
import esphome.config_validation as cv
from esphome.const import CONF_ID

DEPENDENCIES = ["display"]
CONF_DISPLAY = "display"

ns = cg.esphome_ns.namespace("skippy_puppet")
SkippyPuppet = ns.class_("SkippyPuppet", cg.Component)

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(SkippyPuppet),
    cv.Required(CONF_DISPLAY): cv.use_id(display.Display),
}).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_display(await cg.get_variable(config[CONF_DISPLAY])))
