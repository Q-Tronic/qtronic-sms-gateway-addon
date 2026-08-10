#ifdef USE_ESP8266
#include "uart_component_esp8266.h"
#include "esphome/core/application.h"
#include "esphome/core/defines.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#ifdef USE_UART_WAKE_LOOP_ON_RX
#include "esphome/core/wake.h"
#endif

#ifdef USE_LOGGER
#include "esphome/components/logger/logger.h"
#endif

namespace esphome::uart {

static const char *const TAG = "uart.arduino_esp8266";
bool ESP8266UartComponent::serial0_in_use = false;  // NOLINT(cppcoreguidelines-avoid-non-const-global-variables)

uint32_t ESP8266UartComponent::get_config() {
  uint32_t config = 0;

  if (this->parity_ == UART_CONFIG_PARITY_NONE) {
    config |= UART_PARITY_NONE;
  } else if (this->parity_ == UART_CONFIG_PARITY_EVEN) {
    config |= UART_PARITY_EVEN;
  } else if (this->parity_ == UART_CONFIG_PARITY_ODD) {
    config |= UART_PARITY_ODD;
  }

  switch (this->data_bits_) {
    case 5:
      config |= UART_NB_BIT_5;
      break;
    case 6:
      config |= UART_NB_BIT_6;
      break;
    case 7:
      config |= UART_NB_BIT_7;
      break;
    case 8:
      config |= UART_NB_BIT_8;
      break;
  }

  if (this->stop_bits_ == 1) {
    config |= UART_NB_STOP_BIT_1;
  } else {
    config |= UART_NB_STOP_BIT_2;
  }

  if (this->tx_pin_ != nullptr && this->tx_pin_->is_inverted())
    config |= BIT(22);
  if (this->rx_pin_ != nullptr && this->rx_pin_->is_inverted())
    config |= BIT(19);

  return config;
}

void ESP8266UartComponent::setup() {
  auto setup_pin_if_needed = [](InternalGPIOPin *pin) {
    if (!pin) {
      return;
    }
    const auto mask = gpio::Flags::FLAG_OPEN_DRAIN | gpio::Flags::FLAG_PULLUP | gpio::Flags::FLAG_PULLDOWN;
    if ((pin->get_flags() & mask) != gpio::Flags::FLAG_NONE) {
      pin->setup();
    }
  };

  setup_pin_if_needed(this->rx_pin_);
  if (this->rx_pin_ != this->tx_pin_) {
    setup_pin_if_needed(this->tx_pin_);
  }

  // Use Arduino HardwareSerial UARTs if all used pins match the ones
  // preconfigured by the platform. For example if RX disabled but TX pin
  // is 1 we still want to use Serial.
  SerialConfig config = static_cast<SerialConfig>(get_config());

#ifdef USE_ESP8266_UART_SERIAL
  if (!ESP8266UartComponent::serial0_in_use && (tx_pin_ == nullptr || tx_pin_->get_pin() == 1) &&
      (rx_pin_ == nullptr || rx_pin_->get_pin() == 3)
#ifdef USE_LOGGER
      // we will use UART0 if logger isn't using it in swapped mode
      && (logger::global_logger->get_hw_serial() == nullptr ||
          logger::global_logger->get_uart() != logger::UART_SELECTION_UART0_SWAP)
#endif
  ) {
    this->hw_serial_ = &Serial;
    this->hw_serial_->begin(this->baud_rate_, config);
    this->hw_serial_->setRxBufferSize(this->rx_buffer_size_);
    ESP8266UartComponent::serial0_in_use = true;
  } else if (!ESP8266UartComponent::serial0_in_use && (tx_pin_ == nullptr || tx_pin_->get_pin() == 15) &&
             (rx_pin_ == nullptr || rx_pin_->get_pin() == 13)
#ifdef USE_LOGGER
             // we will use UART0 swapped if logger isn't using it in regular mode
             && (logger::global_logger->get_hw_serial() == nullptr ||
                 logger::global_logger->get_uart() != logger::UART_SELECTION_UART0)
#endif
  ) {
    this->hw_serial_ = &Serial;
    this->hw_serial_->begin(this->baud_rate_, config);
    this->hw_serial_->setRxBufferSize(this->rx_buffer_size_);
    this->hw_serial_->swap();
    ESP8266UartComponent::serial0_in_use = true;
  } else
#endif  // USE_ESP8266_UART_SERIAL
#ifdef USE_ESP8266_UART_SERIAL1
      if ((tx_pin_ == nullptr || tx_pin_->get_pin() == 2) && (rx_pin_ == nullptr || rx_pin_->get_pin() == 8)) {
    this->hw_serial_ = &Serial1;
    this->hw_serial_->begin(this->baud_rate_, config);
    this->hw_serial_->setRxBufferSize(this->rx_buffer_size_);
  } else
#endif  // USE_ESP8266_UART_SERIAL1
  {
    this->sw_serial_ = new ESP8266SoftwareSerial();  // NOLINT
    if (!this->sw_serial_->setup(tx_pin_, rx_pin_, this->baud_rate_, this->stop_bits_, this->data_bits_,
                                 this->parity_, this->rx_buffer_size_)) {
      ESP_LOGE(TAG, "Failed to initialize edge-buffered software serial");
      this->mark_failed();
    }
  }
}

void ESP8266UartComponent::load_settings(bool dump_config) {
  ESP_LOGCONFIG(TAG, "Loading UART bus settings");
  if (this->hw_serial_ != nullptr) {
    SerialConfig config = static_cast<SerialConfig>(get_config());
    this->hw_serial_->begin(this->baud_rate_, config);
    this->hw_serial_->setRxBufferSize(this->rx_buffer_size_);
  } else {
    if (!this->sw_serial_->setup(this->tx_pin_, this->rx_pin_, this->baud_rate_, this->stop_bits_, this->data_bits_,
                                 this->parity_, this->rx_buffer_size_)) {
      ESP_LOGE(TAG, "Failed to reload edge-buffered software serial");
      this->mark_failed();
    }
  }
  if (dump_config) {
    ESP_LOGCONFIG(TAG, "UART bus was reloaded.");
    this->dump_config();
  }
}

void ESP8266UartComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "UART Bus:");
  LOG_PIN("  TX Pin: ", this->tx_pin_);
  LOG_PIN("  RX Pin: ", this->rx_pin_);
  if (this->rx_pin_ != nullptr) {
    ESP_LOGCONFIG(TAG, "  RX Buffer Size: %u", this->rx_buffer_size_);  // NOLINT
  }
  ESP_LOGCONFIG(TAG,
                "  Baud Rate: %u baud\n"
                "  Data Bits: %u\n"
                "  Parity: %s\n"
                "  Stop bits: %u",
                this->baud_rate_, this->data_bits_, LOG_STR_ARG(parity_to_str(this->parity_)), this->stop_bits_);
  if (this->hw_serial_ != nullptr) {
    ESP_LOGCONFIG(TAG, "  Using hardware serial interface.");
  } else {
    ESP_LOGCONFIG(TAG, "  Using edge-buffered EspSoftwareSerial");
  }
  this->check_logger_conflict();
}

void ESP8266UartComponent::check_logger_conflict() {
#ifdef USE_LOGGER
  if (this->hw_serial_ == nullptr || logger::global_logger->get_baud_rate() == 0) {
    return;
  }

  if (this->hw_serial_ == logger::global_logger->get_hw_serial()) {
    ESP_LOGW(TAG, "  You're using the same serial port for logging and the UART component. Please "
                  "disable logging over the serial port by setting logger->baud_rate to 0.");
  }
#endif
}

void ESP8266UartComponent::write_array(const uint8_t *data, size_t len) {
  if (this->hw_serial_ != nullptr) {
    this->hw_serial_->write(data, len);
  } else {
    this->sw_serial_->write_array(data, len);
  }
#ifdef USE_UART_DEBUGGER
  for (size_t i = 0; i < len; i++) {
    this->debug_callback_.call(UART_DIRECTION_TX, data[i]);
  }
#endif
}
bool ESP8266UartComponent::peek_byte(uint8_t *data) {
  if (!this->check_read_timeout_())
    return false;
  if (this->hw_serial_ != nullptr) {
    *data = this->hw_serial_->peek();
  } else {
    *data = this->sw_serial_->peek_byte();
  }
  return true;
}
bool ESP8266UartComponent::read_array(uint8_t *data, size_t len) {
  if (!this->check_read_timeout_(len))
    return false;
  if (this->hw_serial_ != nullptr) {
    this->hw_serial_->readBytes(data, len);
  } else {
    for (size_t i = 0; i < len; i++)
      data[i] = this->sw_serial_->read_byte();
  }
#ifdef USE_UART_DEBUGGER
  for (size_t i = 0; i < len; i++) {
    this->debug_callback_.call(UART_DIRECTION_RX, data[i]);
  }
#endif
  return true;
}
size_t ESP8266UartComponent::available() {
  if (this->hw_serial_ != nullptr) {
    return this->hw_serial_->available();
  } else {
    return this->sw_serial_->available();
  }
}
UARTFlushResult ESP8266UartComponent::flush() {
  ESP_LOGVV(TAG, "    Flushing");
  if (this->hw_serial_ != nullptr) {
    this->hw_serial_->flush();
  } else {
    this->sw_serial_->flush();
  }
  return UARTFlushResult::UART_FLUSH_RESULT_ASSUMED_SUCCESS;
}
EspSoftwareSerial::Config ESP8266SoftwareSerial::get_config_(uint8_t stop_bits, uint32_t data_bits,
                                                             UARTParityOptions parity) {
  uint16_t config = static_cast<uint16_t>(data_bits - 5);
  if (parity == UART_CONFIG_PARITY_EVEN) {
    config |= EspSoftwareSerial::PARITY_EVEN;
  } else if (parity == UART_CONFIG_PARITY_ODD) {
    config |= EspSoftwareSerial::PARITY_ODD;
  }
  if (stop_bits == 2)
    config |= 0200;
  return static_cast<EspSoftwareSerial::Config>(config);
}

bool ESP8266SoftwareSerial::setup(InternalGPIOPin *tx_pin, InternalGPIOPin *rx_pin, uint32_t baud_rate,
                                  uint8_t stop_bits, uint32_t data_bits, UARTParityOptions parity,
                                  size_t rx_buffer_size) {
  if (this->initialized_) {
    this->serial_.end();
    this->initialized_ = false;
  }

  const bool tx_inverted = tx_pin != nullptr && tx_pin->is_inverted();
  const bool rx_inverted = rx_pin != nullptr && rx_pin->is_inverted();
  if (tx_pin != nullptr && rx_pin != nullptr && tx_inverted != rx_inverted) {
    ESP_LOGE(TAG, "EspSoftwareSerial requires matching TX/RX inversion");
    return false;
  }

  const int8_t tx_number = tx_pin == nullptr ? -1 : static_cast<int8_t>(tx_pin->get_pin());
  const int8_t rx_number = rx_pin == nullptr ? -1 : static_cast<int8_t>(rx_pin->get_pin());
  const bool inverted = tx_pin != nullptr ? tx_inverted : rx_inverted;
  const bool rx_pullup =
      rx_pin != nullptr && (rx_pin->get_flags() & gpio::Flags::FLAG_PULLUP) != gpio::Flags::FLAG_NONE;
  const bool tx_open_drain =
      tx_pin != nullptr &&
      (tx_pin->get_flags() & gpio::Flags::FLAG_OPEN_DRAIN) != gpio::Flags::FLAG_NONE;

  this->serial_.enableRxGPIOPullUp(rx_pullup);
  this->serial_.enableTxGPIOOpenDrain(tx_open_drain);
  this->serial_.enableIntTx(true);

  // The ISR stores only edge timestamps. Decoding happens from available()/read()
  // in the normal ESPHome loop, so the Wi-Fi interrupt is never blocked for a
  // complete UART frame. Four edge slots per requested RX byte provide over
  // 100 ms of worst-case edge buffering at this gateway's 9600 baud.
  const int isr_buffer_size = static_cast<int>(rx_buffer_size * 4);
  this->serial_.begin(baud_rate, get_config_(stop_bits, data_bits, parity), rx_number, tx_number, inverted,
                      static_cast<int>(rx_buffer_size), isr_buffer_size);
  this->initialized_ = static_cast<bool>(this->serial_);
  return this->initialized_;
}

uint8_t ESP8266SoftwareSerial::read_byte() {
  const int value = this->serial_.read();
  return value < 0 ? 0 : static_cast<uint8_t>(value);
}
uint8_t ESP8266SoftwareSerial::peek_byte() {
  const int value = this->serial_.peek();
  return value < 0 ? 0 : static_cast<uint8_t>(value);
}
void ESP8266SoftwareSerial::write_byte(uint8_t data) { this->serial_.write(data); }
void ESP8266SoftwareSerial::write_array(const uint8_t *data, size_t len) { this->serial_.write(data, len); }
void ESP8266SoftwareSerial::flush() {
  // Flush is a NO-OP with software serial, all bytes are written immediately.
}
size_t ESP8266SoftwareSerial::available() { return static_cast<size_t>(this->serial_.available()); }

}  // namespace esphome::uart
#endif  // USE_ESP8266
