"""skippy_stream: shows a face that skippy-pc draws, by receiving the changed tiles of each frame over UDP and writing them straight to the display.

The PC sends 24x20-pixel RGB565 tiles (only the ones that changed since the last frame): 8-byte header (b'S', flags, x u16, y u16, w u8, h u8; flag 1 =
last tile of a frame) then w*h*2 bytes. No decoding on the box, so it is as fast as the Wi-Fi.
"""
import esphome.codegen as cg
from esphome.components import display
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_PORT

DEPENDENCIES = ["display"]
CONF_DISPLAY = "display"

ns = cg.esphome_ns.namespace("skippy_stream")
SkippyStream = ns.class_("SkippyStream", cg.Component)

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(SkippyStream),
    cv.Required(CONF_DISPLAY): cv.use_id(display.Display),
    cv.Optional(CONF_PORT, default=10803): cv.port,
}).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_display(await cg.get_variable(config[CONF_DISPLAY])))
    cg.add(var.set_port(config[CONF_PORT]))
