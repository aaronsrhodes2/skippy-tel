#include "skippy_puppet.h"
#include "esphome/core/log.h"
#include "esphome/core/hal.h"
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <math.h>
#include <string.h>
#define protected public                      // to reach the panel's frame buffer, as skippy_stream does
#include "esphome/components/mipi_rgb/mipi_rgb.h"
#undef protected
#include "scene_data.h"

namespace esphome {
namespace skippy_puppet {

static const char *const TAG = "puppet";

static inline int imin(int a, int b) { return a < b ? a : b; }
static inline int imax(int a, int b) { return a > b ? a : b; }
static inline float fclamp(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

void SkippyPuppet::setup() {
  this->ops_ = (Op *) heap_caps_malloc(sizeof(Op) * MAX_OPS, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  this->cov_ = (uint8_t *) heap_caps_malloc(STRIP_PX, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  this->strip_ = (uint16_t *) heap_caps_malloc(STRIP_PX * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  this->prof_ = (uint32_t *) heap_caps_calloc(MAX_OPS, sizeof(uint32_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  if (this->ops_ == nullptr || this->cov_ == nullptr || this->strip_ == nullptr || this->prof_ == nullptr) { ESP_LOGE(TAG, "no memory"); this->mark_failed(); }
}

int SkippyPuppet::parse_(const uint8_t *b, size_t len) {
  if (len < 6 || b[0] != 'P' || b[1] != 3) return -1;
  int n = b[4] | (b[5] << 8);
  size_t p = 6;
  int k = 0;
  for (int i = 0; i < n && k < MAX_OPS; i++) {
    Op &o = this->ops_[k];
    memset(&o, 0, sizeof(Op) - sizeof(o.pts));
    if (p >= len) return -2;
    o.kind = b[p];
    if (o.kind == 1) {
      if (p + 8 > len) return -3;
      o.npts = b[p + 1]; o.width = b[p + 2]; o.rounded = b[p + 3]; o.r = b[p + 4]; o.g = b[p + 5]; o.b = b[p + 6]; o.opa = b[p + 7];
      p += 8;
      if (o.npts > MAX_PTS || p + 4u * o.npts > len) return -4;
      int x0 = 9999, y0 = 9999, x1 = -9999, y1 = -9999;
      for (int j = 0; j < o.npts; j++, p += 4) {
        int x = (int16_t) (b[p] | (b[p + 1] << 8)), y = (int16_t) (b[p + 2] | (b[p + 3] << 8));
        o.pts[2 * j] = x; o.pts[2 * j + 1] = y;
        x0 = imin(x0, x); y0 = imin(y0, y); x1 = imax(x1, x); y1 = imax(y1, y);
      }
      int pad = o.width / 2 + 2;
      o.bx0 = x0 - pad; o.by0 = y0 - pad; o.bx1 = x1 + pad; o.by1 = y1 + pad;
    } else if (o.kind == 2) {
      if (p + 19 > len) return -5;
      o.x = (int16_t) (b[p + 1] | (b[p + 2] << 8)); o.y = (int16_t) (b[p + 3] | (b[p + 4] << 8));
      o.w = b[p + 5] | (b[p + 6] << 8); o.h = b[p + 7] | (b[p + 8] << 8); o.radius = b[p + 9] | (b[p + 10] << 8);
      o.border = b[p + 11]; o.r = b[p + 12]; o.g = b[p + 13]; o.b = b[p + 14]; o.opa = b[p + 15]; o.br = b[p + 16]; o.bg = b[p + 17]; o.bb = b[p + 18];
      p += 19;
      o.bx0 = o.x - 1; o.by0 = o.y - 1; o.bx1 = o.x + o.w + 1; o.by1 = o.y + o.h + 1;
    } else if (o.kind == 3) {
      if (p + 7 > len) return -6;
      o.sprite = b[p + 1]; o.x = (int16_t) (b[p + 2] | (b[p + 3] << 8)); o.y = (int16_t) (b[p + 4] | (b[p + 5] << 8)); o.opa = b[p + 6];
      p += 7;
      o.w = SPRITE0_W; o.h = SPRITE0_H;
      o.bx0 = o.x; o.by0 = o.y; o.bx1 = o.x + o.w; o.by1 = o.y + o.h;
    } else return -7;
    k++;
  }
  this->nops_ = k;
  return k;
}

inline void SkippyPuppet::blend_(int x, int y, uint8_t r, uint8_t g, uint8_t b, uint32_t a) {
  uint16_t *dst = this->dst_ + (y - this->oy_) * this->dstride_ + (x - this->ox_);
  if (a >= 255) { uint16_t v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3); *dst = (uint16_t) ((v >> 8) | (v << 8)); return; }
  uint16_t wv = *dst, px = (uint16_t) ((wv >> 8) | (wv << 8));
  uint32_t r0 = (px >> 11) & 31, g0 = (px >> 5) & 63, b0 = px & 31;
  r0 = (r0 << 3) | (r0 >> 2); g0 = (g0 << 2) | (g0 >> 4); b0 = (b0 << 3) | (b0 >> 2);
  uint32_t ia = 255 - a;
  uint32_t nr = (r * a + r0 * ia) / 255, ng = (g * a + g0 * ia) / 255, nb = (b * a + b0 * ia) / 255;
  uint16_t v = (uint16_t) (((nr >> 3) << 11) | ((ng >> 2) << 5) | (nb >> 3));
  *dst = (uint16_t) ((v >> 8) | (v << 8));
}

// A polyline of constant width as a capsule chain: each pixel's coverage is how far its centre is inside half the width of the nearest segment (the
// maximum over the segments, so a joint is not blended twice), then it is mixed in at the op's opacity. A pixel clearly inside (distance < hw - 0.5) or
// clearly outside (> hw + 0.5) is decided on the squared distance, so only the one-pixel edge pays for a square root.
void SkippyPuppet::line_(const Op &o, const Rect &c) {
  if (o.npts < 2 || o.opa == 0) return;
  int x0 = imax(c.x0, o.bx0), y0 = imax(c.y0, o.by0), x1 = imin(c.x1, o.bx1 + 1), y1 = imin(c.y1, o.by1 + 1);
  if (x1 <= x0 || y1 <= y0) return;
  int bw = x1 - x0;
  memset(this->cov_, 0, (size_t) bw * (y1 - y0));
  float hw = o.width / 2.0f;
  float in2 = hw > 0.5f ? (hw - 0.5f) * (hw - 0.5f) : -1.0f, out2 = (hw + 0.5f) * (hw + 0.5f);
  for (int s = 0; s + 1 < o.npts; s++) {
    float ax = o.pts[2 * s], ay = o.pts[2 * s + 1], bx = o.pts[2 * s + 2], by = o.pts[2 * s + 3];
    float dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy, il2 = l2 > 0 ? 1.0f / l2 : 0.0f;
    int sx0 = imax(x0, (int) floorf(fminf(ax, bx) - hw - 1)), sx1 = imin(x1, (int) ceilf(fmaxf(ax, bx) + hw + 2));
    int sy0 = imax(y0, (int) floorf(fminf(ay, by) - hw - 1)), sy1 = imin(y1, (int) ceilf(fmaxf(ay, by) + hw + 2));
    for (int y = sy0; y < sy1; y++) {
      uint8_t *row = this->cov_ + (size_t) (y - y0) * bw - x0;
      float py = y - ay;
      for (int x = sx0; x < sx1; x++) {
        float px = x - ax;
        float t = fclamp((px * dx + py * dy) * il2, 0.0f, 1.0f);
        float ex = px - t * dx, ey = py - t * dy;
        float d2 = ex * ex + ey * ey;
        if (d2 >= out2) continue;
        uint8_t cb;
        if (d2 <= in2) cb = 255;
        else cb = (uint8_t) (fclamp(hw + 0.5f - sqrtf(d2), 0.0f, 1.0f) * 255.0f + 0.5f);
        if (cb > row[x]) row[x] = cb;
      }
    }
  }
  for (int y = y0; y < y1; y++) {
    const uint8_t *row = this->cov_ + (size_t) (y - y0) * bw - x0;
    for (int x = x0; x < x1; x++)
      if (row[x]) this->blend_(x, y, o.r, o.g, o.b, o.opa == 255 ? row[x] : ((uint32_t) row[x] * o.opa + 127) / 255);
  }
}

static inline float ell_cov(float dx, float dy, float rx, float ry) {
  float fx = dx / rx, fy = dy / ry, f2 = fx * fx + fy * fy;
  if (f2 < 1e-6f) return 1.0f;
  float f = sqrtf(f2);
  float gx = dx / (rx * rx), gy = dy / (ry * ry);
  float g = sqrtf(gx * gx + gy * gy) / f;
  return fclamp(0.5f - (f - 1.0f) / g, 0.0f, 1.0f);
}

// An ellipse's pixels are decided on f2 = (dx/rx)^2 + (dy/ry)^2 first: well inside (f2 < tin) is full coverage, well outside (f2 > tout) is nothing,
// and only the thin band between them pays for the exact edge.
struct Ell {
  float rx, ry, tin, tout;
  Ell(float rx_, float ry_) : rx(rx_), ry(ry_) {
    float band = 1.5f / fminf(rx_, ry_);
    tin = (1.0f - band) > 0 ? (1.0f - band) * (1.0f - band) : -1.0f;
    tout = (1.0f + band) * (1.0f + band);
  }
  inline float cov(float dx, float dy) const {
    float fx = dx / rx, fy = dy / ry, f2 = fx * fx + fy * fy;
    if (f2 > tout) return 0.0f;
    if (f2 < tin) return 1.0f;
    return ell_cov(dx, dy, rx, ry);
  }
};

void SkippyPuppet::box_(const Op &o, const Rect &c) {
  if (o.opa == 0 || o.w == 0 || o.h == 0) return;
  int x0 = imax(c.x0, o.bx0), y0 = imax(c.y0, o.by0), x1 = imin(c.x1, o.bx1 + 1), y1 = imin(c.y1, o.by1 + 1);
  if (x1 <= x0 || y1 <= y0) return;
  bool ell = o.radius >= 100 || (o.radius * 2 >= imin(o.w, o.h) && o.radius > 0);
  if (!ell) {
    int rx0 = imax(x0, o.x), ry0 = imax(y0, o.y), rx1 = imin(x1, o.x + o.w), ry1 = imin(y1, o.y + o.h);
    for (int y = ry0; y < ry1; y++)
      for (int x = rx0; x < rx1; x++) this->blend_(x, y, o.r, o.g, o.b, o.opa);
    return;
  }
  int cx = o.x + o.w / 2, cy = o.y + o.h / 2;
  Ell outer((float) imax(1, o.w / 2), (float) imax(1, o.h / 2)), inner((float) imax(1, o.w / 2 - o.border), (float) imax(1, o.h / 2 - o.border));
  for (int y = y0; y < y1; y++)
    for (int x = x0; x < x1; x++) {
      float ao = outer.cov((float) (x - cx), (float) (y - cy));
      if (ao <= 0.0f) continue;
      uint8_t r = o.r, g = o.g, b = o.b;
      if (o.border) {
        float ai = inner.cov((float) (x - cx), (float) (y - cy));
        r = (uint8_t) (o.br + (o.r - o.br) * ai); g = (uint8_t) (o.bg + (o.g - o.bg) * ai); b = (uint8_t) (o.bb + (o.b - o.bb) * ai);
      }
      this->blend_(x, y, r, g, b, (uint32_t) (ao * o.opa + 0.5f));
    }
}

void SkippyPuppet::sprite_(const Op &o, const Rect &c) {
  if (o.opa == 0 || o.sprite != 0) return;
  int y0 = imax(c.y0, o.y), y1 = imin(c.y1, o.y + SPRITE0_H);
  for (int y = y0; y < y1; y++) {
    int sy = y - o.y;
    int x0 = imax(c.x0, o.x + (int) SPRITE0_X0[sy]), x1 = imin(c.x1, o.x + (int) SPRITE0_X1[sy] + 1);       // only the columns that hold anything
    if (x1 <= x0) continue;
    const uint8_t *ar = SPRITE0_A + (size_t) sy * SPRITE0_W - o.x;
    const uint8_t *pr = SPRITE0_RGB + (size_t) sy * SPRITE0_W * 2 - o.x * 2;
    uint16_t *dst = this->dst_ + (y - this->oy_) * this->dstride_ - this->ox_;
    for (int x = x0; x < x1; x++) {
      uint32_t a = ar[x];
      if (o.opa != 255) a = (a * o.opa + 127) / 255;
      if (a == 0) continue;
      uint16_t wv = (uint16_t) (pr[2 * x] | (pr[2 * x + 1] << 8));
      if (a >= 255) { dst[x] = wv; continue; }
      uint16_t px = (uint16_t) ((wv >> 8) | (wv << 8));
      uint32_t r = (px >> 11) & 31, g = (px >> 5) & 63, b = px & 31;
      this->blend_(x, y, (uint8_t) ((r << 3) | (r >> 2)), (uint8_t) ((g << 2) | (g >> 4)), (uint8_t) ((b << 3) | (b >> 2)), a);
    }
  }
}

static uint32_t t_line, t_box, t_sprite;

void SkippyPuppet::draw_ops_(const Rect &c) {
  for (int i = 0; i < this->nops_; i++) {
    const Op &o = this->ops_[i];
    if (o.bx1 < c.x0 || o.bx0 >= c.x1 || o.by1 < c.y0 || o.by0 >= c.y1) continue;
    int64_t t0 = esp_timer_get_time();
    if (o.kind == 1) this->line_(o, c);
    else if (o.kind == 2) this->box_(o, c);
    else this->sprite_(o, c);
    uint32_t dt = (uint32_t) (esp_timer_get_time() - t0);
    this->prof_[i] += dt;
    (o.kind == 1 ? t_line : (o.kind == 2 ? t_box : t_sprite)) += dt;
  }
}

// Compose a rectangle in internal RAM, strip by strip, and write each strip to the panel with one copy: the panel's frame buffer is in PSRAM, where
// the read-modify-write of every blended pixel was most of the time.
void SkippyPuppet::render_(const Rect &r) {
  int w = r.x1 - r.x0;
  if (w <= 0 || r.y1 <= r.y0) return;
  int rows = imax(1, STRIP_PX / w);
  for (int y = r.y0; y < r.y1; y += rows) {
    Rect s = {r.x0, y, r.x1, imin(y + rows, r.y1)};
    this->dst_ = this->strip_; this->ox_ = s.x0; this->oy_ = s.y0; this->dstride_ = w;
    memset(this->strip_, 0, (size_t) w * (s.y1 - s.y0) * 2);
    this->draw_ops_(s);
    for (int yy = s.y0; yy < s.y1; yy++) memcpy(this->fb_ + yy * W + s.x0, this->strip_ + (size_t) (yy - s.y0) * w, (size_t) w * 2);
  }
}

void SkippyPuppet::bench() {
  if (this->is_failed()) return;
  if (this->fb_ == nullptr) {
    auto *m = static_cast<mipi_rgb::MipiRgb *>(this->display_);
    if (m->fb_ != nullptr && m->buffer_ == nullptr) this->fb_ = m->fb_;
  }
  if (this->fb_ == nullptr) { ESP_LOGE(TAG, "no panel frame buffer"); return; }
  int n = this->parse_(SCENE, sizeof(SCENE));
  if (n < 0) { ESP_LOGE(TAG, "scene did not parse (%d)", n); return; }
  Rect full = {0, 0, W, H};
  const int R = 8;
  t_line = t_box = t_sprite = 0;
  memset(this->prof_, 0, sizeof(uint32_t) * MAX_OPS);
  int64_t a = esp_timer_get_time();
  for (int i = 0; i < R; i++) this->render_(full);
  int64_t e = esp_timer_get_time();
  ESP_LOGI(TAG, "%d ops: whole scene %u us (lines %u, boxes %u, sprite %u us)", n, (unsigned) ((e - a) / R), (unsigned) (t_line / R), (unsigned) (t_box / R), (unsigned) (t_sprite / R));
  for (int rank = 0; rank < 6; rank++) {                       // the slowest ops: what is left to speed up
    int best = -1;
    for (int i = 0; i < this->nops_; i++) if (this->prof_[i] > 0 && (best < 0 || this->prof_[i] > this->prof_[best])) best = i;
    if (best < 0) break;
    const Op &o = this->ops_[best];
    ESP_LOGI(TAG, "  slow op %d kind %d %dx%d (w %d, pts %d, opa %d): %u us", best, o.kind, o.bx1 - o.bx0, o.by1 - o.by0, o.width ? o.width : o.border, o.npts, o.opa, (unsigned) (this->prof_[best] / R));
    this->prof_[best] = 0;
  }
  Rect dirty = {176, 192, 304, 288};                           // a 128 x 96 patch around the face's middle
  t_line = t_box = t_sprite = 0;
  a = esp_timer_get_time();
  const int D = 40;
  for (int i = 0; i < D; i++) this->render_(dirty);
  e = esp_timer_get_time();
  ESP_LOGI(TAG, "dirty 128x96: %u us per redraw (lines %u, boxes %u, sprite %u us)", (unsigned) ((e - a) / D), (unsigned) (t_line / D), (unsigned) (t_box / D), (unsigned) (t_sprite / D));
  this->render_(full);
  this->shown_ = true;
}

}  // namespace skippy_puppet
}  // namespace esphome
