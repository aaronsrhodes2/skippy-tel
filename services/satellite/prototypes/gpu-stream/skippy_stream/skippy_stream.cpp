#include "skippy_stream.h"
#include "esphome/core/log.h"
#include "esphome/core/hal.h"
#include <lwip/sockets.h>
#include <fcntl.h>
#include "miniz.h"
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace esphome {
namespace skippy_stream {

static const char *const TAG = "skippy_stream";

void SkippyStream::setup() {
  this->sock_ = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
  if (this->sock_ < 0) { ESP_LOGE(TAG, "no socket"); this->mark_failed(); return; }
  int rcvbuf = 65536;
  ::setsockopt(this->sock_, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));
  struct sockaddr_in addr = {};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_ANY);
  addr.sin_port = htons(this->port_);
  if (::bind(this->sock_, (struct sockaddr *) &addr, sizeof(addr)) < 0) { ESP_LOGE(TAG, "bind failed"); this->mark_failed(); return; }
  ESP_LOGI(TAG, "listening for tiles on UDP %u", this->port_);
}

// The receiving runs in its own task: the box's main loop only turns every few milliseconds, and the network stack keeps just a few packets for a socket.
void SkippyStream::task_(void *arg) { static_cast<SkippyStream *>(arg)->run_(); }

void SkippyStream::run_() {
  struct timeval tv = {0, 100000};
  ::setsockopt(this->sock_, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
  uint8_t buf[1600], pix[24 * 20 * 2 + 64];
  for (;;) {
    int n = ::recv(this->sock_, buf, sizeof(buf), 0);
    if (n < 2 || !this->active_ || buf[0] != 'T') continue;
    int pos = 2;
    while (pos + 8 <= n) {                                       // records: x y (u16) w h (u8) clen (u16) then clen bytes of raw deflate
      uint16_t x = buf[pos] | (buf[pos + 1] << 8), y = buf[pos + 2] | (buf[pos + 3] << 8), clen = buf[pos + 6] | (buf[pos + 7] << 8);
      uint8_t w = buf[pos + 4], h = buf[pos + 5];
      pos += 8;
      if (pos + clen > n || (size_t) w * h * 2 > sizeof(pix)) break;
      size_t got = tinfl_decompress_mem_to_mem(pix, sizeof(pix), buf + pos, clen, 0);
      if (got == (size_t) w * h * 2) {
        this->display_->draw_pixels_at(x, y, w, h, pix, display::COLOR_ORDER_RGB, display::COLOR_BITNESS_565, true);
        this->tiles_++;
      }
      pos += clen;
    }
    if (buf[1] & 1) this->frames_++;
  }
}

void SkippyStream::loop() {
  if (this->sock_ < 0) return;
  if (!this->started_) { this->started_ = true; xTaskCreatePinnedToCore(SkippyStream::task_, "skippy_stream", 4096, this, 6, nullptr, 0); }
  if (this->active_ && millis() - this->last_log_ > 5000) {
    ESP_LOGI(TAG, "%.1f frames/s, %u tiles/s", this->frames_ / 5.0f, (unsigned) (this->tiles_ / 5));
    this->frames_ = this->tiles_ = 0;
    this->last_log_ = millis();
  }
}

}  // namespace skippy_stream
}  // namespace esphome
