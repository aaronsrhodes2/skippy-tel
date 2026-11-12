#pragma once
#include "esphome/core/component.h"
#include "esphome/components/display/display.h"

namespace esphome {
namespace skippy_puppet {

static const int W = 480, H = 480, MAX_OPS = 256, MAX_PTS = 64;

struct Op {
  uint8_t kind;                       // 1 polyline, 2 box, 3 sprite
  uint8_t npts, width, rounded, r, g, b, opa, border, br, bg, bb, sprite;
  int16_t x, y;                       // box / sprite origin
  uint16_t w, h, radius;
  int16_t bx0, by0, bx1, by1;         // bounding box (inclusive), for dirty-rectangle tests
  int16_t pts[2 * MAX_PTS];
};

struct Rect { int x0, y0, x1, y1; };  // [x0, x1) x [y0, y1)

class SkippyPuppet : public Component {
 public:
  void set_display(display::Display *d) { this->display_ = d; }
  void setup() override;
  float get_setup_priority() const override { return setup_priority::LATE; }
  bool shown() const { return this->shown_; }
  void bench();                       // draw the built-in scene whole and by dirty rectangle, log the timing, leave it on the panel
  void release() { this->shown_ = false; }

 protected:
  display::Display *display_{nullptr};
  uint16_t *fb_{nullptr};             // the panel's frame buffer (byte-swapped RGB565 words)
  uint8_t *cov_{nullptr};             // coverage scratch for one polyline, W*H bytes
  Op *ops_{nullptr};
  int nops_{0};
  bool shown_{false};
  int parse_(const uint8_t *buf, size_t len);
  void clear_(const Rect &c);
  void draw_ops_(const Rect &c);
  void line_(const Op &o, const Rect &c);
  void box_(const Op &o, const Rect &c);
  void sprite_(const Op &o, const Rect &c);
  inline void blend_(int x, int y, uint8_t r, uint8_t g, uint8_t b, uint32_t a);   // a: 0..255
};

}  // namespace skippy_puppet
}  // namespace esphome
