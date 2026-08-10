#pragma once

#ifdef USE_ESP8266

#include <HardwareSerial.h>
#include <SoftwareSerial.h>
#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "uart_component.h"

namespace esphome::uart {

class ESP8266SoftwareSerial {
 public:
  bool setup(InternalGPIOPin *tx_pin, InternalGPIOPin *rx_pin, uint32_t baud_rate, uint8_t stop_bits,
             uint32_t data_bits, UARTParityOptions parity, size_t rx_buffer_size);

  uint8_t read_byte();
  uint8_t peek_byte();

  void flush();

  void write_byte(uint8_t data);
  void write_array(const uint8_t *data, size_t len);

  size_t available();

 protected:
  static EspSoftwareSerial::Config get_config_(uint8_t stop_bits, uint32_t data_bits, UARTParityOptions parity);

  EspSoftwareSerial::UART serial_;
  bool initialized_{false};
};

class ESP8266UartComponent final : public UARTComponent, public Component {
 public:
  void setup() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::BUS; }

  void write_array(const uint8_t *data, size_t len) override;

  bool peek_byte(uint8_t *data) override;
  bool read_array(uint8_t *data, size_t len) override;

  size_t available() override;
  UARTFlushResult flush() override;

  uint32_t get_config();

  /**
   * Load the UART with the current settings.
   * @param dump_config (Optional, default `true`): True for displaying new settings or
   * false to change it quitely
   *
   * Example:
   * ```cpp
   * id(uart1).load_settings();
   * ```
   *
   * This will load the current UART interface with the latest settings (baud_rate, parity, etc).
   */
  void load_settings(bool dump_config) override;
  using UARTComponent::load_settings;  // also bring in the no-arg overload for convenience

 protected:
  void check_logger_conflict() override;

  HardwareSerial *hw_serial_{nullptr};
  ESP8266SoftwareSerial *sw_serial_{nullptr};

 private:
  static bool serial0_in_use;  // NOLINT(cppcoreguidelines-avoid-non-const-global-variables)
};

}  // namespace esphome::uart
#endif  // USE_ESP8266
