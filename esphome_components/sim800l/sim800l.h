#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <utility>

#include "esphome/core/automation.h"
#include "esphome/core/component.h"
#include "esphome/core/defines.h"
#ifdef USE_BINARY_SENSOR
#include "esphome/components/binary_sensor/binary_sensor.h"
#endif
#ifdef USE_SENSOR
#include "esphome/components/sensor/sensor.h"
#endif
#ifdef USE_TEXT_SENSOR
#include "esphome/components/text_sensor/text_sensor.h"
#endif
#include "esphome/components/uart/uart.h"

namespace esphome::sim800l {

inline constexpr size_t SIM800L_READ_BUFFER_LENGTH = 1024;
inline constexpr size_t SIM800L_SMS_QUEUE_CAPACITY = 4;
inline constexpr size_t SIM800L_MAX_RECIPIENT_LENGTH = 96;
inline constexpr size_t SIM800L_MAX_GSM_SEPTETS = 160;
inline constexpr size_t SIM800L_MAX_UCS2_MESSAGE_HEX_LENGTH = 280;

enum class SmsEncoding : uint8_t { GSM = 0, UCS2 };

enum State : uint8_t {
  STATE_BOOT_WAIT = 0,
  STATE_WAIT_AT,
  STATE_WAIT_ATE0,
  STATE_WAIT_INIT_CMGF,
  STATE_WAIT_INIT_CHARSET,
  STATE_WAIT_INIT_CSMP,
  STATE_WAIT_CLIP,
  STATE_WAIT_CMEE,
  STATE_WAIT_CUSD_ENABLE,
  STATE_READY,
  STATE_WAIT_CREG,
  STATE_WAIT_CSQ,
  STATE_WAIT_SMS_LIST,
  STATE_WAIT_SMS_DELETE,
  STATE_WAIT_CALL_LIST,
  STATE_SMS_WAIT_CMGF,
  STATE_SMS_WAIT_CHARSET,
  STATE_SMS_WAIT_CSMP,
  STATE_SMS_WAIT_PROMPT,
  STATE_SMS_WAIT_RESULT,
  STATE_SMS_WAIT_FINAL_OK,
  STATE_SMS_WAIT_RESTORE_CHARSET,
  STATE_SMS_WAIT_RESTORE_CSMP,
  STATE_DIAL_WAIT,
  STATE_ANSWER_WAIT,
  STATE_HANGUP_WAIT,
  STATE_USSD_WAIT_CHARSET,
  STATE_USSD_WAIT_ACK,
};

struct SmsRequest {
  std::string recipient;
  std::string message;
  SmsEncoding encoding{SmsEncoding::GSM};
  uint8_t attempts{0};
};

class Sim800LComponent final : public uart::UARTDevice, public PollingComponent {
 public:
  void setup() override;
  void update() override;
  void loop() override;
  void dump_config() override;

#ifdef USE_BINARY_SENSOR
  void set_registered_binary_sensor(binary_sensor::BinarySensor *sensor) { this->registered_binary_sensor_ = sensor; }
#endif

#ifdef USE_SENSOR
  void set_rssi_sensor(sensor::Sensor *sensor) { this->rssi_sensor_ = sensor; }
  void set_sms_queue_depth_sensor(sensor::Sensor *sensor) { this->sms_queue_depth_sensor_ = sensor; }
  void set_sms_sent_count_sensor(sensor::Sensor *sensor) { this->sms_sent_count_sensor_ = sensor; }
  void set_sms_failed_count_sensor(sensor::Sensor *sensor) { this->sms_failed_count_sensor_ = sensor; }
  void set_sms_unknown_count_sensor(sensor::Sensor *sensor) { this->sms_unknown_count_sensor_ = sensor; }
  void set_sim800_timeout_count_sensor(sensor::Sensor *sensor) { this->sim800_timeout_count_sensor_ = sensor; }
  void set_sim800_recovery_count_sensor(sensor::Sensor *sensor) { this->sim800_recovery_count_sensor_ = sensor; }
  void set_uart_rx_overflow_count_sensor(sensor::Sensor *sensor) { this->uart_rx_overflow_count_sensor_ = sensor; }
  void set_sim800_last_response_age_sensor(sensor::Sensor *sensor) {
    this->sim800_last_response_age_sensor_ = sensor;
  }
#endif

#ifdef USE_TEXT_SENSOR
  void set_sms_status_text_sensor(text_sensor::TextSensor *sensor) { this->sms_status_text_sensor_ = sensor; }
  void set_sms_last_error_text_sensor(text_sensor::TextSensor *sensor) {
    this->sms_last_error_text_sensor_ = sensor;
  }
  void set_sim800_state_text_sensor(text_sensor::TextSensor *sensor) { this->sim800_state_text_sensor_ = sensor; }
#endif

  template<typename F> void add_on_sms_received_callback(F &&callback) {
    this->sms_received_callback_.add(std::forward<F>(callback));
  }
  template<typename F> void add_on_incoming_call_callback(F &&callback) {
    this->incoming_call_callback_.add(std::forward<F>(callback));
  }
  template<typename F> void add_on_call_connected_callback(F &&callback) {
    this->call_connected_callback_.add(std::forward<F>(callback));
  }
  template<typename F> void add_on_call_disconnected_callback(F &&callback) {
    this->call_disconnected_callback_.add(std::forward<F>(callback));
  }
  template<typename F> void add_on_ussd_received_callback(F &&callback) {
    this->ussd_received_callback_.add(std::forward<F>(callback));
  }

  // Existing public API remains source-compatible. Requests are now bounded
  // and serialized by the component instead of writing directly to UART.
  void send_sms(const std::string &recipient, const std::string &message);
  void send_sms_unicode(const std::string &recipient, const std::string &message);
  void send_ussd(const std::string &ussd_code);
  void dial(const std::string &recipient);
  void connect();
  void disconnect();

  uint32_t get_last_response_ms() const { return this->last_response_ms_; }
  size_t get_sms_queue_depth() const { return this->sms_queue_size_ + (this->sms_active_ ? 1U : 0U); }
  uint32_t get_timeout_count() const { return this->timeout_count_; }
  uint32_t get_recovery_count() const { return this->recovery_count_; }
  uint32_t get_uart_rx_overflow_count() const { return this->uart_rx_overflow_count_; }
  const std::string &get_last_error() const { return this->last_error_; }

 protected:
  static constexpr uint32_t BOOT_DELAY_MS = 2000;
  static constexpr uint32_t COMMAND_TIMEOUT_MS = 10000;
  static constexpr uint32_t PROMPT_TIMEOUT_MS = 15000;
  static constexpr uint32_t SMS_RESULT_TIMEOUT_MS = 65000;
  static constexpr uint32_t SMS_FINAL_OK_TIMEOUT_MS = 10000;
  static constexpr uint32_t MODEM_RETRY_MIN_MS = 5000;
  static constexpr uint32_t MODEM_RETRY_MAX_MS = 30000;
  static constexpr uint8_t SMS_PRE_SUBMIT_MAX_ATTEMPTS = 2;
  static constexpr size_t MAX_RX_BYTES_PER_LOOP = 256;

  void send_command_(const std::string &command, State next_state, uint32_t timeout_ms = COMMAND_TIMEOUT_MS);
  void send_sms_prompt_command_();
  void send_sms_body_();
  void set_state_(State state, uint32_t timeout_ms = 0);
  const char *state_name_(State state) const;

  void process_line_(const std::string &line);
  void process_ok_();
  void process_error_(const std::string &line);
  bool process_urc_(const std::string &line);
  void process_deadline_();
  void process_ready_();

  void start_probe_();
  void start_regular_poll_();
  void start_sms_list_();
  void start_call_list_();
  void start_active_sms_();
  void start_ussd_();
  void finish_ready_();
  void recover_(const std::string &reason, bool timeout);

  bool enqueue_sms_(const std::string &recipient, const std::string &message, SmsEncoding encoding);
  bool activate_next_sms_();
  bool requeue_active_sms_();
  void complete_active_sms_(const char *status, const std::string &error = {});
  void publish_sms_status_(const std::string &status);
  void publish_last_error_(const std::string &error);
  void publish_queue_depth_();
  void publish_counters_();
  void publish_last_response_age_();

  bool validate_sms_(const std::string &recipient, const std::string &message, SmsEncoding encoding,
                     std::string *error) const;
  bool validate_dial_string_(const std::string &value) const;
  bool validate_ussd_(const std::string &value) const;
  static size_t gsm_payload_units_(const std::string &value);
  static bool is_ucs2_hex_(const std::string &value);

  void reset_sms_list_parser_();
  bool parse_sms_header_(const std::string &line);
  void process_sms_list_line_(const std::string &line);
  void finish_received_sms_();
  void parse_creg_(const std::string &line);
  void parse_csq_(const std::string &line);
  void parse_clcc_(const std::string &line);
  void parse_clip_(const std::string &line);
  void parse_ussd_(const std::string &line);
  void set_registered_(bool registered);

#ifdef USE_BINARY_SENSOR
  binary_sensor::BinarySensor *registered_binary_sensor_{nullptr};
#endif

#ifdef USE_SENSOR
  sensor::Sensor *rssi_sensor_{nullptr};
  sensor::Sensor *sms_queue_depth_sensor_{nullptr};
  sensor::Sensor *sms_sent_count_sensor_{nullptr};
  sensor::Sensor *sms_failed_count_sensor_{nullptr};
  sensor::Sensor *sms_unknown_count_sensor_{nullptr};
  sensor::Sensor *sim800_timeout_count_sensor_{nullptr};
  sensor::Sensor *sim800_recovery_count_sensor_{nullptr};
  sensor::Sensor *uart_rx_overflow_count_sensor_{nullptr};
  sensor::Sensor *sim800_last_response_age_sensor_{nullptr};
#endif

#ifdef USE_TEXT_SENSOR
  text_sensor::TextSensor *sms_status_text_sensor_{nullptr};
  text_sensor::TextSensor *sms_last_error_text_sensor_{nullptr};
  text_sensor::TextSensor *sim800_state_text_sensor_{nullptr};
#endif

  State state_{STATE_BOOT_WAIT};
  uint32_t state_deadline_ms_{0};
  uint32_t last_response_ms_{0};
  uint32_t modem_retry_ms_{MODEM_RETRY_MIN_MS};
  bool initialized_{false};
  bool registered_{false};
  bool poll_requested_{true};
  bool sms_poll_requested_{false};
  std::string last_command_;

  char read_buffer_[SIM800L_READ_BUFFER_LENGTH]{};
  size_t read_pos_{0};
  bool discard_line_{false};
  bool ignore_prompt_space_{false};

  std::array<SmsRequest, SIM800L_SMS_QUEUE_CAPACITY> sms_queue_{};
  size_t sms_queue_head_{0};
  size_t sms_queue_size_{0};
  SmsRequest active_sms_{};
  bool sms_active_{false};
  bool sms_body_submitted_{false};
  bool sms_reference_seen_{false};

  uint16_t sms_list_index_{0};
  uint16_t sms_delete_index_{0};
  bool sms_list_has_message_{false};
  bool sms_list_ignore_rest_{false};
  std::string sms_sender_;
  std::string sms_message_;
  std::string sms_pending_line_;
  bool sms_pending_line_valid_{false};

  std::string ussd_;
  bool ussd_pending_{false};
  std::string dial_recipient_;
  bool dial_pending_{false};
  bool connect_pending_{false};
  bool disconnect_pending_{false};

  bool creg_seen_{false};
  bool creg_registered_{false};
  bool call_list_seen_{false};
  uint8_t call_state_{6};
  bool incoming_call_notified_{false};

  uint32_t sms_sent_count_{0};
  uint32_t sms_failed_count_{0};
  uint32_t sms_unknown_count_{0};
  uint32_t timeout_count_{0};
  uint32_t recovery_count_{0};
  uint32_t uart_rx_overflow_count_{0};
  std::string sms_status_{"idle"};
  std::string last_error_{"none"};

  CallbackManager<void(std::string, std::string)> sms_received_callback_;
  CallbackManager<void(std::string)> incoming_call_callback_;
  CallbackManager<void()> call_connected_callback_;
  CallbackManager<void()> call_disconnected_callback_;
  CallbackManager<void(std::string)> ussd_received_callback_;
};

template<typename... Ts> class Sim800LSendSmsAction final : public Action<Ts...> {
 public:
  explicit Sim800LSendSmsAction(Sim800LComponent *parent) : parent_(parent) {}
  TEMPLATABLE_VALUE(std::string, recipient)
  TEMPLATABLE_VALUE(std::string, message)

  void play(const Ts &...x) override {
    this->parent_->send_sms(this->recipient_.value(x...), this->message_.value(x...));
  }

 protected:
  Sim800LComponent *parent_;
};

template<typename... Ts> class Sim800LSendSmsUnicodeAction final : public Action<Ts...> {
 public:
  explicit Sim800LSendSmsUnicodeAction(Sim800LComponent *parent) : parent_(parent) {}
  TEMPLATABLE_VALUE(std::string, recipient)
  TEMPLATABLE_VALUE(std::string, message)

  void play(const Ts &...x) override {
    this->parent_->send_sms_unicode(this->recipient_.value(x...), this->message_.value(x...));
  }

 protected:
  Sim800LComponent *parent_;
};

template<typename... Ts> class Sim800LSendUssdAction final : public Action<Ts...> {
 public:
  explicit Sim800LSendUssdAction(Sim800LComponent *parent) : parent_(parent) {}
  TEMPLATABLE_VALUE(std::string, ussd)

  void play(const Ts &...x) override { this->parent_->send_ussd(this->ussd_.value(x...)); }

 protected:
  Sim800LComponent *parent_;
};

template<typename... Ts> class Sim800LDialAction final : public Action<Ts...> {
 public:
  explicit Sim800LDialAction(Sim800LComponent *parent) : parent_(parent) {}
  TEMPLATABLE_VALUE(std::string, recipient)

  void play(const Ts &...x) override { this->parent_->dial(this->recipient_.value(x...)); }

 protected:
  Sim800LComponent *parent_;
};

template<typename... Ts> class Sim800LConnectAction final : public Action<Ts...> {
 public:
  explicit Sim800LConnectAction(Sim800LComponent *parent) : parent_(parent) {}
  void play(const Ts &...x) override { this->parent_->connect(); }

 protected:
  Sim800LComponent *parent_;
};

template<typename... Ts> class Sim800LDisconnectAction final : public Action<Ts...> {
 public:
  explicit Sim800LDisconnectAction(Sim800LComponent *parent) : parent_(parent) {}
  void play(const Ts &...x) override { this->parent_->disconnect(); }

 protected:
  Sim800LComponent *parent_;
};

}  // namespace esphome::sim800l
