#include "skippy_stream.h"
#include "esphome/core/log.h"
#include "esphome/core/hal.h"
#include <lwip/sockets.h>
#include <esp_heap_caps.h>
#define protected public                      // to read the panel's frame buffer for screenshots; the layout does not change
#include "esphome/components/mipi_rgb/mipi_rgb.h"
#undef protected
extern "C" {
#include "miniz.h"                           // the ROM's tinfl (the S3 ROM has it)
}
#include <freertos/task.h>
#include <string.h>
#include <esp_timer.h>

namespace esphome {
namespace skippy_stream {

static const char *const TAG = "skippy_stream";
static const int HEAD = 7;                     // 'K', 2, flags, frame u16, seq u16

void SkippyStream::setup() {
  this->lock_ = xSemaphoreCreateMutex();
  this->free_q_ = xQueueCreate(NBUF, sizeof(Item));
  this->ready_q_ = xQueueCreate(NBUF, sizeof(Item));
  bool ok = this->lock_ && this->free_q_ && this->ready_q_;
  for (int i = 0; ok && i < NBUF; i++) {
    this->bufs_[i] = (uint8_t *) heap_caps_malloc(STAGE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ok = this->bufs_[i] != nullptr;
    Item it = {(uint8_t) i, 0};
    if (ok) xQueueSend(this->free_q_, &it, 0);
  }
  this->pix_ = (uint8_t *) heap_caps_malloc(MAX_TILE * MAX_TILE * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  this->rx_ = (uint8_t *) heap_caps_malloc(1600, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  this->inflater_ = heap_caps_malloc(sizeof(tinfl_decompressor), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);   // ~11 KB: it overflowed the old 4 KB task stack
  if (!ok || !this->pix_ || !this->rx_ || !this->inflater_) { ESP_LOGE(TAG, "no memory"); this->mark_failed(); return; }
  this->sock_ = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
  if (this->sock_ < 0) { ESP_LOGE(TAG, "no socket"); this->mark_failed(); return; }
  int rcvbuf = 32768;
  ::setsockopt(this->sock_, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));
  struct sockaddr_in addr = {};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_ANY);
  addr.sin_port = htons(this->port_);
  if (::bind(this->sock_, (struct sockaddr *) &addr, sizeof(addr)) < 0) { ESP_LOGE(TAG, "bind failed"); this->mark_failed(); return; }
  ESP_LOGI(TAG, "listening for tiles on UDP %u", this->port_);
}

void SkippyStream::set_ready(bool r) {
  xSemaphoreTake(this->lock_, portMAX_DELAY);              // wait out a frame being drawn
  this->ready_ = r;
  if (!r) { this->stage_len_ = 0; this->have_frame_ = false; this->have_seq_ = false; }
  xSemaphoreGive(this->lock_);
}

void SkippyStream::recv_task_(void *arg) { static_cast<SkippyStream *>(arg)->run_(); }
void SkippyStream::draw_task_(void *arg) { static_cast<SkippyStream *>(arg)->draw_loop_(); }

// The receiving task only receives: it checks the header, keeps each datagram's records in a buffer and hands a finished frame on. It never draws
// (drawing in here let datagrams pile up beyond what the network stack holds, and they were lost).
void SkippyStream::run_() {
  struct timeval tv = {0, 100000};
  ::setsockopt(this->sock_, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
  for (;;) {
    int n = ::recv(this->sock_, this->rx_, 1600, 0);
    if (n < HEAD) continue;
    this->accept_(this->rx_, n);
  }
}

void SkippyStream::finish_() {
  if (this->cur_ < 0) return;
  Item it = {(uint8_t) this->cur_, (uint32_t) this->stage_len_};
  xQueueSend(this->ready_q_, &it, 0);
  this->cur_ = -1;
  this->stage_len_ = 0;
}

void SkippyStream::accept_(const uint8_t *buf, int n) {
  if (buf[0] != 'K' || buf[1] != 2) { this->bad_++; return; }
  this->rx_ms_ = millis();
  this->rx_ever_ = true;
  this->dgrams_++;
  uint16_t seq = buf[5] | (buf[6] << 8);
  if (this->have_seq_) this->missing_ += (uint16_t) (seq - this->next_seq_);        // datagrams that never arrived
  this->have_seq_ = true;
  this->next_seq_ = seq + 1;
  if (!this->ready_) return;                               // LVGL still owns the screen: only the arrival counts (it makes wants() true)
  uint8_t flags = buf[2];
  uint16_t frame = buf[3] | (buf[4] << 8);
  if (this->have_frame_ && frame != this->cur_frame_) {    // the previous frame's last datagram was lost: draw what arrived (the sender's rolling refresh heals the rest)
    this->lost_++;
    this->finish_();
  }
  this->cur_frame_ = frame;
  this->have_frame_ = true;
  size_t body = n - HEAD;
  if (this->cur_ >= 0 && this->stage_len_ + body > STAGE_BYTES) this->finish_();    // (a frame this big is a keyframe: draw it in pieces rather than drop it)
  if (this->cur_ < 0) {
    Item it;
    if (xQueueReceive(this->free_q_, &it, 0) != pdTRUE) { this->dropped_++; return; }   // the drawing task is far behind: this part of the frame is lost, the refresh heals it
    this->cur_ = it.idx;
    this->stage_len_ = 0;
  }
  memcpy(this->bufs_[this->cur_] + this->stage_len_, buf + HEAD, body);
  this->stage_len_ += body;
  if (flags & 1) { this->finish_(); this->have_frame_ = false; this->frames_++; }
}

void SkippyStream::draw_loop_() {
  for (;;) {
    Item it;
    if (xQueueReceive(this->ready_q_, &it, portMAX_DELAY) != pdTRUE) continue;
    this->draw_(this->bufs_[it.idx], it.len);
    it.len = 0;
    xQueueSend(this->free_q_, &it, 0);
  }
}

void SkippyStream::draw_(const uint8_t *data, size_t len) {
  xSemaphoreTake(this->lock_, portMAX_DELAY);
  if (this->ready_) {
    const uint8_t *p = data, *end = data + len;
    auto *dec = (tinfl_decompressor *) this->inflater_;
    while (end - p >= 8) {                                // a record: x y (u16), w h (u8), n (u16), data
      int x = p[0] | (p[1] << 8), y = p[2] | (p[3] << 8), w = p[4], h = p[5];
      uint32_t nn = p[6] | (p[7] << 8);
      p += 8;
      bool raw = nn & 0x8000, solid = !raw && (nn & 0x4000), runs = !raw && !solid && (nn & 0x2000);
      size_t rlen = raw ? (nn & 0x7fff) : (solid ? (nn & 0x3fff) : (runs ? (nn & 0x1fff) : nn)), want = (size_t) w * h * 2;
      if (rlen > (size_t) (end - p)) { this->bad_++; break; }        // the record runs past the datagrams: the rest is garbage
      bool ok = w > 0 && h > 0 && w <= MAX_TILE && h <= MAX_TILE && x + w <= W && y + h <= H;
      if (ok && raw) ok = rlen == want;
      if (ok && solid) ok = rlen == 2;
      if (ok && runs) {                                    // (count, word) triples that must fill the tile exactly
        ok = rlen % 3 == 0;
        size_t total = 0;
        for (size_t i = 0; ok && i < rlen; i += 3) { if (p[i] == 0) ok = false; total += p[i]; }
        ok = ok && total == (size_t) w * h;
      }
      int64_t t0 = esp_timer_get_time();
      if (ok && raw) memcpy(this->pix_, p, want);
      else if (ok && solid) {
        for (size_t i = 0; i < want; i += 2) { this->pix_[i] = p[0]; this->pix_[i + 1] = p[1]; }
      } else if (ok && runs) {
        uint8_t *o = this->pix_;
        for (size_t i = 0; i < rlen; i += 3) { for (int k = 0; k < p[i]; k++) { *o++ = p[i + 1]; *o++ = p[i + 2]; } }
      } else if (ok) {
        ok = rlen < 0x2000;
        tinfl_init(dec);
        size_t in_n = rlen, out_n = MAX_TILE * MAX_TILE * 2;
        tinfl_status st = tinfl_decompress(dec, p, &in_n, this->pix_, this->pix_, &out_n, TINFL_FLAG_USING_NON_WRAPPING_OUTPUT_BUF);
        ok = st == TINFL_STATUS_DONE && out_n == want;
      }
      int64_t t1 = esp_timer_get_time();
      if (ok) {
        if (this->fb_ != nullptr) {
          for (int r = 0; r < h; r++) memcpy(this->fb_ + (size_t) (y + r) * W + x, this->pix_ + (size_t) r * w * 2, (size_t) w * 2);
        } else {
          this->display_->draw_pixels_at(x, y, w, h, this->pix_, display::COLOR_ORDER_RGB, display::COLOR_BITNESS_565, true);
        }
        this->inflate_us_ += (uint32_t) (t1 - t0);
        this->blit_us_ += (uint32_t) (esp_timer_get_time() - t1);
        this->tiles_++;
      } else this->bad_++;
      p += rlen;
    }
  }
  xSemaphoreGive(this->lock_);
}

void SkippyStream::loop() {
  if (this->is_failed() || this->sock_ < 0) return;
  if (!this->started_) {
    this->started_ = true;
    auto *m = static_cast<mipi_rgb::MipiRgb *>(this->display_);
    if (m->fb_ != nullptr && m->buffer_ == nullptr) this->fb_ = m->fb_;
    ESP_LOGI(TAG, "drawing %s", this->fb_ != nullptr ? "straight into the panel frame buffer" : "through the display API");
    xTaskCreatePinnedToCore(SkippyStream::recv_task_, "skippy_rx", 4096, this, 7, nullptr, 0);
    xTaskCreatePinnedToCore(SkippyStream::draw_task_, "skippy_draw", 8192, this, 8, nullptr, 1);
  }
  if (this->ready_ && millis() - this->last_log_ > 5000) {
    ESP_LOGI(TAG, "%.1f frames/s, %u dgrams/s, %u tiles/s (inflate %u us, blit %u us per tile), missing %u, partial frames %u, dropped %u, bad %u", this->frames_ / 5.0f, (unsigned) (this->dgrams_ / 5),
             (unsigned) (this->tiles_ / 5), (unsigned) (this->inflate_us_ / (this->tiles_ + 1)), (unsigned) (this->blit_us_ / (this->tiles_ + 1)), (unsigned) this->missing_, (unsigned) this->lost_, (unsigned) this->dropped_, (unsigned) this->bad_);
    this->frames_ = this->tiles_ = this->dgrams_ = this->missing_ = this->lost_ = this->dropped_ = this->inflate_us_ = this->blit_us_ = 0;
    this->last_log_ = millis();
  }
}

uint8_t *SkippyStream::snapshot(size_t *len) {
  auto *m = static_cast<mipi_rgb::MipiRgb *>(this->display_);
  if (m == nullptr || m->fb_ == nullptr) return nullptr;
  size_t n = (size_t) W * H;
  auto *out = (uint8_t *) heap_caps_malloc(n * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (out == nullptr) return nullptr;
  for (size_t i = 0; i < n; i++) { uint16_t v = m->fb_[i]; out[2 * i] = v >> 8; out[2 * i + 1] = v & 0xff; }    // the panel holds the streamed big-endian bytes; LVGL snapshots are little-endian
  *len = n * 2;
  return out;
}

}  // namespace skippy_stream
}  // namespace esphome
