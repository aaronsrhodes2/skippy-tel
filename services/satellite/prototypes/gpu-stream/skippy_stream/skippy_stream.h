#pragma once
#include "esphome/core/component.h"
#include "esphome/components/display/display.h"

namespace esphome {
namespace skippy_stream {

class SkippyStream : public Component {
 public:
  void set_display(display::Display *d) { this->display_ = d; }
  void set_port(uint16_t p) { this->port_ = p; }
  void setup() override;
  void loop() override;
  float get_setup_priority() const override { return setup_priority::LATE; }
  void set_active(bool a) { this->active_ = a; }
  bool active() const { return this->active_; }

 protected:
  display::Display *display_{nullptr};
  uint16_t port_{10803};
  int sock_{-1};
  bool active_{false}, started_{false};
  static void task_(void *arg);
  void run_();
  volatile uint32_t frames_{0}, tiles_{0};
  uint32_t last_log_{0};
  uint8_t buf_[1600];
};

}  // namespace skippy_stream
}  // namespace esphome
