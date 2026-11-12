#pragma once
#include "esphome/core/component.h"
#include "esphome/components/display/display.h"
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>

namespace esphome {
namespace skippy_puppet {

static const int W = 480, H = 480, MAX_OPS = 256, MAX_PTS = 64;
static const int STRIP_PX = 8192;                 // one strip (per core) is composed in internal RAM (16 KB) and written to the panel once

struct Op {
  uint8_t kind;                       // 1 polyline, 2 box, 3 sprite
  uint8_t npts, width, rounded, r, g, b, opa, border, br, bg, bb, sprite;
  int16_t x, y;                       // box / sprite origin
  uint16_t w, h, radius;
  int16_t bx0, by0, bx1, by1;         // bounding box (inclusive), for dirty-rectangle tests
  int16_t pts[2 * MAX_PTS];
};

struct Rect { int x0, y0, x1, y1; };  // [x0, x1) x [y0, y1)

// What one core needs to compose a strip: its own scratch and where its pixels go. Two cores each own one, so they never share a write.
struct Ctx {
  uint16_t *strip{nullptr};           // internal RAM: the strip being composed
  uint8_t *cov{nullptr};              // internal RAM: coverage scratch for one polyline in a strip
  uint16_t *dst{nullptr};             // where blending writes: the strip, whose top-left is (ox, oy) and whose rows are `stride` words
  int ox{0}, oy{0}, stride{W};
  uint32_t t_line{0}, t_box{0}, t_sprite{0};
};

class SkippyPuppet : public Component {
 public:
  void set_display(display::Display *d) { this->display_ = d; }
  void setup() override;
  float get_setup_priority() const override { return setup_priority::LATE; }
  bool shown() const { return this->shown_; }
  void bench();                       // draw the built-in scene whole and by dirty rectangle, one core and two, log the timing, leave it on the panel
  void release() { this->shown_ = false; }

 protected:
  display::Display *display_{nullptr};
  uint16_t *fb_{nullptr};             // the panel's frame buffer (byte-swapped RGB565 words)
  Ctx ctx_[2];                        // [0] the calling core, [1] the worker's
  Op *ops_{nullptr};
  uint32_t *prof_{nullptr};           // microseconds per op, summed over a bench run (only the calling core's strips count)
  int nops_{0};
  bool shown_{false};
  bool dual_{true};                   // use the worker core (bench turns it off to compare)
  TaskHandle_t worker_{nullptr};
  SemaphoreHandle_t go_{nullptr}, done_{nullptr};
  Rect job_{0, 0, 0, 0};
  int parse_(const uint8_t *buf, size_t len);
  static void worker_main_(void *self);
  void render_(const Rect &r);        // compose r strip by strip (clear, every op that touches it, in order) and write each strip to the panel
  void strips_(const Rect &r, int first, int step, Ctx &c, bool profile);
  void draw_ops_(Ctx &c, const Rect &r, bool profile);
  void line_(Ctx &c, const Op &o, const Rect &r);
  void box_(Ctx &c, const Op &o, const Rect &r);
  void sprite_(Ctx &c, const Op &o, const Rect &r);
};

}  // namespace skippy_puppet
}  // namespace esphome
