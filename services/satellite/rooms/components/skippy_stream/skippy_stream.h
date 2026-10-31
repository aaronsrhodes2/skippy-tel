#pragma once
#include "esphome/core/component.h"
#include "esphome/components/display/display.h"
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/queue.h>

namespace esphome {
namespace skippy_stream {

static const int W = 480, H = 480, MAX_TILE = 64;
static const size_t STAGE_BYTES = 64 * 1024;       // one frame's compressed records, held until its last datagram arrives and then drawn together (no half-drawn frames)
static const int NBUF = 6;                           // frames in flight: the receiving task fills one while the drawing task draws another

class SkippyStream : public Component {
 public:
  void set_display(display::Display *d) { this->display_ = d; }
  void set_port(uint16_t p) { this->port_ = p; }
  void set_timeout(uint32_t ms) { this->timeout_ms_ = ms; }
  void setup() override;
  void loop() override;
  float get_setup_priority() const override { return setup_priority::LATE; }
  // wants(): valid datagrams arrived lately and streaming is switched on. ready(): the owner (the yaml) has paused LVGL, so the task may draw.
  bool wants() const { return this->enabled_ && this->rx_ever_ && (millis() - this->rx_ms_) < this->timeout_ms_; }
  bool ready() const { return this->ready_; }
  void set_ready(bool r);
  void set_enabled(bool e) { this->enabled_ = e; }
  // a copy of the panel's frame buffer as little-endian RGB565 (what lv_snapshot gives), for the screenshot action; free() it. nullptr when not possible.
  uint8_t *snapshot(size_t *len);

 protected:
  struct Item {
    uint8_t idx;
    uint32_t len;
  };
  display::Display *display_{nullptr};
  uint16_t port_{10803};
  uint32_t timeout_ms_{1200};
  int sock_{-1};
  bool enabled_{true}, started_{false};
  volatile bool ready_{false}, rx_ever_{false};
  volatile uint32_t rx_ms_{0};
  SemaphoreHandle_t lock_{nullptr};
  uint8_t *bufs_[NBUF] = {};
  uint8_t *pix_{nullptr}, *rx_{nullptr};
  uint16_t *fb_{nullptr};                            // the panel's frame buffer, written directly (draw_pixels_at is far slower); nullptr: use the display API
  volatile uint32_t inflate_us_{0}, blit_us_{0};
  void *inflater_{nullptr};
  QueueHandle_t free_q_{nullptr}, ready_q_{nullptr};
  int cur_{-1};                                      // the buffer being filled (an index), -1: none yet
  size_t stage_len_{0};
  uint16_t cur_frame_{0}, next_seq_{0};
  bool have_frame_{false}, have_seq_{false};
  volatile uint32_t dgrams_{0}, frames_{0}, tiles_{0}, bad_{0}, lost_{0}, missing_{0}, dropped_{0};
  uint32_t last_log_{0};
  static void recv_task_(void *arg);
  static void draw_task_(void *arg);
  void run_();
  void draw_loop_();
  void finish_();                                    // hand the buffer being filled to the drawing task
  void draw_(const uint8_t *data, size_t len);
  void accept_(const uint8_t *buf, int n);
};

}  // namespace skippy_stream
}  // namespace esphome
