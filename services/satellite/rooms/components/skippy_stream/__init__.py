"""skippy_stream: shows a face that skippy-pc draws, by receiving the changed tiles of each frame over UDP and writing them straight to the panel.

Protocol v2 (little-endian), one frame = one or more datagrams: 'K', version 2, flags (bit 0 = last datagram of the frame), frame u16, seq u16 (every datagram, so gaps are counted), then records:
x u16, y u16, w u8, h u8, n u16 and the data. n & 0x8000: the low 15 bits are w*h*2 and that many raw RGB565 bytes follow (big-endian, the panel's native
order); n & 0x4000: solid, the low bits are 2 and one word fills the tile; n & 0x2000: runs, (count u8, word) triples that fill the tile exactly; otherwise n bytes of raw deflate. Every field is validated before it is used; a stalled stream hands the screen back to LVGL (see
skippy-room.yaml).
"""
import esphome.codegen as cg
from esphome.components import display
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_PORT

DEPENDENCIES = ["display"]
CONF_DISPLAY = "display"
CONF_TIMEOUT = "timeout"

ns = cg.esphome_ns.namespace("skippy_stream")
SkippyStream = ns.class_("SkippyStream", cg.Component)

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(SkippyStream),
    cv.Required(CONF_DISPLAY): cv.use_id(display.Display),
    cv.Optional(CONF_PORT, default=10803): cv.port,
    cv.Optional(CONF_TIMEOUT, default="1200ms"): cv.positive_time_period_milliseconds,
}).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_display(await cg.get_variable(config[CONF_DISPLAY])))
    cg.add(var.set_port(config[CONF_PORT]))
    cg.add(var.set_timeout(config[CONF_TIMEOUT].total_milliseconds))
