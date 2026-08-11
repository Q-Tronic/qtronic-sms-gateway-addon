#include "sim800l.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstring>

#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

namespace esphome::sim800l {

static const char *const TAG = "sim800l";

static constexpr uint8_t ASCII_CR = 0x0D;
static constexpr uint8_t ASCII_LF = 0x0A;
static constexpr uint8_t ASCII_ESC = 0x1B;
static constexpr uint8_t ASCII_CTRL_Z = 0x1A;

namespace {

bool deadline_reached(uint32_t now, uint32_t deadline) {
  return deadline != 0 && static_cast<int32_t>(now - deadline) >= 0;
}

std::string trim_copy(const std::string &value) {
  const auto first = value.find_first_not_of(" \t");
  if (first == std::string::npos)
    return {};
  const auto last = value.find_last_not_of(" \t");
  return value.substr(first, last - first + 1);
}

bool is_modem_error(const std::string &line) {
  return line == "ERROR" || line.starts_with("+CME ERROR:") || line.starts_with("+CMS ERROR:");
}

}  // namespace

void Sim800LComponent::setup() {
  this->publish_sms_status_("idle");
  this->publish_last_error_("none");
  this->publish_queue_depth_();
  this->publish_counters_();
  this->set_registered_(false);
  this->set_state_(STATE_BOOT_WAIT, BOOT_DELAY_MS);
}

void Sim800LComponent::update() {
  this->poll_requested_ = true;
  this->publish_last_response_age_();
  if (this->state_ == STATE_READY)
    this->process_ready_();
}

void Sim800LComponent::loop() {
  // Bound work in one ESPHome loop iteration. A noisy RX line must not starve
  // Wi-Fi/API housekeeping on the ESP8266.
  size_t processed_bytes = 0;
  while (processed_bytes++ < MAX_RX_BYTES_PER_LOOP && this->available()) {
    uint8_t byte{0};
    if (!this->read_byte(&byte)) {
      this->uart_rx_overflow_count_++;
      this->publish_counters_();
      break;
    }

    if (this->discard_line_) {
      if (byte == ASCII_LF) {
        this->discard_line_ = false;
        this->read_pos_ = 0;
        this->recover_("uart_rx_line_overflow", false);
      }
      continue;
    }

    if (byte == ASCII_CR)
      continue;

    if (this->state_ == STATE_SMS_WAIT_PROMPT && this->read_pos_ == 0 && byte == '>') {
      this->last_response_ms_ = millis();
      this->ignore_prompt_space_ = true;
      this->send_sms_body_();
      continue;
    }

    if (this->ignore_prompt_space_) {
      this->ignore_prompt_space_ = false;
      if (byte == ' ')
        continue;
    }

    if (byte >= 0x7F)
      byte = '?';

    if (byte == ASCII_LF) {
      this->read_buffer_[this->read_pos_] = 0;
      std::string line(this->read_buffer_, this->read_pos_);
      this->read_pos_ = 0;
      this->process_line_(line);
      continue;
    }

    if (this->read_pos_ >= SIM800L_READ_BUFFER_LENGTH - 1) {
      this->discard_line_ = true;
      this->read_pos_ = 0;
      this->uart_rx_overflow_count_++;
      this->publish_counters_();
      continue;
    }
    this->read_buffer_[this->read_pos_++] = static_cast<char>(byte);
  }

  this->process_deadline_();
  if (this->state_ == STATE_READY)
    this->process_ready_();
}

void Sim800LComponent::send_command_(const std::string &command, State next_state, uint32_t timeout_ms) {
  ESP_LOGV(TAG, "TX [%s]: %s", this->state_name_(next_state), command.c_str());
  this->last_command_ = command;
  this->write_str(command.c_str());
  this->write_byte(ASCII_CR);
  this->write_byte(ASCII_LF);
  this->set_state_(next_state, timeout_ms);
}

void Sim800LComponent::send_sms_prompt_command_() {
  if (!this->sms_active_)
    return;
  this->send_command_("AT+CMGS=\"" + this->active_sms_.recipient + "\"", STATE_SMS_WAIT_PROMPT,
                      PROMPT_TIMEOUT_MS);
}

void Sim800LComponent::send_sms_body_() {
  if (!this->sms_active_ || this->state_ != STATE_SMS_WAIT_PROMPT)
    return;
  ESP_LOGI(TAG, "Submitting queued %s SMS", this->active_sms_.encoding == SmsEncoding::UCS2 ? "UCS2" : "GSM");
  this->write_str(this->active_sms_.message.c_str());
  this->write_byte(ASCII_CTRL_Z);
  this->sms_body_submitted_ = true;
  this->sms_reference_seen_ = false;
  this->set_state_(STATE_SMS_WAIT_RESULT, SMS_RESULT_TIMEOUT_MS);
}

void Sim800LComponent::set_state_(State state, uint32_t timeout_ms) {
  this->state_ = state;
  this->state_deadline_ms_ = timeout_ms == 0 ? 0 : millis() + timeout_ms;
#ifdef USE_TEXT_SENSOR
  if (this->sim800_state_text_sensor_ != nullptr) {
    const std::string state_name = this->state_name_(state);
    if (!this->sim800_state_text_sensor_->has_state() || this->sim800_state_text_sensor_->state != state_name)
      this->sim800_state_text_sensor_->publish_state(state_name);
  }
#endif
}

const char *Sim800LComponent::state_name_(State state) const {
  switch (state) {
    case STATE_BOOT_WAIT:
      return "boot_wait";
    case STATE_WAIT_AT:
      return "wait_at";
    case STATE_WAIT_ATE0:
      return "wait_ate0";
    case STATE_WAIT_INIT_CMGF:
      return "init_cmgf";
    case STATE_WAIT_INIT_CHARSET:
      return "init_charset";
    case STATE_WAIT_INIT_CSMP:
      return "init_csmp";
    case STATE_WAIT_CLIP:
      return "init_clip";
    case STATE_WAIT_CMEE:
      return "init_cmee";
    case STATE_WAIT_CUSD_ENABLE:
      return "init_cusd";
    case STATE_READY:
      return "ready";
    case STATE_WAIT_CREG:
      return "poll_creg";
    case STATE_WAIT_CSQ:
      return "poll_csq";
    case STATE_WAIT_SMS_LIST:
      return "poll_sms";
    case STATE_WAIT_SMS_DELETE:
      return "delete_sms";
    case STATE_WAIT_CALL_LIST:
      return "poll_calls";
    case STATE_SMS_WAIT_CMGF:
      return "sms_cmgf";
    case STATE_SMS_WAIT_CHARSET:
      return "sms_charset";
    case STATE_SMS_WAIT_CSMP:
      return "sms_csmp";
    case STATE_SMS_WAIT_PROMPT:
      return "sms_prompt";
    case STATE_SMS_WAIT_RESULT:
      return "sms_result";
    case STATE_SMS_WAIT_FINAL_OK:
      return "sms_final_ok";
    case STATE_SMS_WAIT_RESTORE_CHARSET:
      return "sms_restore_charset";
    case STATE_SMS_WAIT_RESTORE_CSMP:
      return "sms_restore_csmp";
    case STATE_DIAL_WAIT:
      return "dial";
    case STATE_ANSWER_WAIT:
      return "answer";
    case STATE_HANGUP_WAIT:
      return "hangup";
    case STATE_USSD_WAIT_CHARSET:
      return "ussd_charset";
    case STATE_USSD_WAIT_ACK:
      return "ussd_ack";
  }
  return "unknown";
}

void Sim800LComponent::process_line_(const std::string &line) {
  if (line.empty()) {
    // An empty first line can be the complete payload of an empty SMS. Other
    // blank CMGL framing lines are intentionally ignored.
    if (this->state_ == STATE_WAIT_SMS_LIST && this->sms_list_has_message_ &&
        !this->sms_pending_line_valid_)
      this->process_sms_list_line_(line);
    return;
  }

  this->last_response_ms_ = millis();
  ESP_LOGV(TAG, "RX [%s]: %s", this->state_name_(this->state_), line.c_str());

  // While parsing CMGL output, message bodies must win over URC-looking text.
  if (this->state_ == STATE_WAIT_SMS_LIST) {
    this->process_sms_list_line_(line);
    return;
  }

  if (!this->last_command_.empty() && line == this->last_command_)
    return;  // command echo before ATE0 takes effect

  if (this->process_urc_(line))
    return;

  if (is_modem_error(line)) {
    this->process_error_(line);
    return;
  }

  if (line == "OK") {
    this->process_ok_();
    return;
  }

  switch (this->state_) {
    case STATE_WAIT_CSQ:
      if (line.starts_with("+CSQ:"))
        this->parse_csq_(line);
      break;
    case STATE_WAIT_CALL_LIST:
      if (line.starts_with("+CLCC:"))
        this->parse_clcc_(line);
      break;
    case STATE_SMS_WAIT_RESULT:
      if (line.starts_with("+CMGS:")) {
        this->sms_reference_seen_ = true;
        this->set_state_(STATE_SMS_WAIT_FINAL_OK, SMS_FINAL_OK_TIMEOUT_MS);
      }
      break;
    default:
      ESP_LOGVV(TAG, "Ignoring line in %s: %s", this->state_name_(this->state_), line.c_str());
      break;
  }
}

void Sim800LComponent::process_ok_() {
  switch (this->state_) {
    case STATE_WAIT_AT:
      this->send_command_("ATE0", STATE_WAIT_ATE0);
      break;
    case STATE_WAIT_ATE0:
      this->send_command_("AT+CMGF=1", STATE_WAIT_INIT_CMGF);
      break;
    case STATE_WAIT_INIT_CMGF:
      this->send_command_("AT+CSCS=\"GSM\"", STATE_WAIT_INIT_CHARSET);
      break;
    case STATE_WAIT_INIT_CHARSET:
      this->send_command_("AT+CSMP=17,167,0,0", STATE_WAIT_INIT_CSMP);
      break;
    case STATE_WAIT_INIT_CSMP:
      this->send_command_("AT+CLIP=1", STATE_WAIT_CLIP);
      break;
    case STATE_WAIT_CLIP:
      this->send_command_("AT+CMEE=2", STATE_WAIT_CMEE);
      break;
    case STATE_WAIT_CMEE:
      this->send_command_("AT+CUSD=1", STATE_WAIT_CUSD_ENABLE);
      break;
    case STATE_WAIT_CUSD_ENABLE:
      this->initialized_ = true;
      this->modem_retry_ms_ = MODEM_RETRY_MIN_MS;
      this->poll_requested_ = true;
      this->finish_ready_();
      break;
    case STATE_WAIT_CREG:
      if (this->creg_seen_)
        this->set_registered_(this->creg_registered_);
      if (this->registered_)
        this->send_command_("AT+CSQ", STATE_WAIT_CSQ);
      else
        this->finish_ready_();
      break;
    case STATE_WAIT_CSQ:
      this->start_sms_list_();
      break;
    case STATE_WAIT_SMS_DELETE:
      this->start_call_list_();
      break;
    case STATE_WAIT_CALL_LIST:
      if (!this->call_list_seen_ && this->call_state_ != 6) {
        this->call_state_ = 6;
        this->incoming_call_notified_ = false;
        this->call_disconnected_callback_.call();
      }
      this->finish_ready_();
      break;
    case STATE_SMS_WAIT_CMGF:
      this->send_command_(this->active_sms_.encoding == SmsEncoding::UCS2 ? "AT+CSCS=\"UCS2\""
                                                                          : "AT+CSCS=\"GSM\"",
                          STATE_SMS_WAIT_CHARSET);
      break;
    case STATE_SMS_WAIT_CHARSET:
      this->send_command_(this->active_sms_.encoding == SmsEncoding::UCS2 ? "AT+CSMP=17,167,0,8"
                                                                          : "AT+CSMP=17,167,0,0",
                          STATE_SMS_WAIT_CSMP);
      break;
    case STATE_SMS_WAIT_CSMP:
      this->send_sms_prompt_command_();
      break;
    case STATE_SMS_WAIT_RESULT: {
      // A bare OK without +CMGS does not prove that the network accepted the SMS.
      const bool unicode = this->sms_active_ && this->active_sms_.encoding == SmsEncoding::UCS2;
      this->complete_active_sms_("unknown", "sms_result_without_reference");
      if (unicode)
        this->send_command_("AT+CSCS=\"GSM\"", STATE_SMS_WAIT_RESTORE_CHARSET);
      else
        this->finish_ready_();
      break;
    }
    case STATE_SMS_WAIT_FINAL_OK: {
      const bool unicode = this->sms_active_ && this->active_sms_.encoding == SmsEncoding::UCS2;
      this->complete_active_sms_("sent");
      if (unicode)
        this->send_command_("AT+CSCS=\"GSM\"", STATE_SMS_WAIT_RESTORE_CHARSET);
      else
        this->finish_ready_();
      break;
    }
    case STATE_SMS_WAIT_RESTORE_CHARSET:
      this->send_command_("AT+CSMP=17,167,0,0", STATE_SMS_WAIT_RESTORE_CSMP);
      break;
    case STATE_SMS_WAIT_RESTORE_CSMP:
      this->finish_ready_();
      break;
    case STATE_DIAL_WAIT:
      this->dial_pending_ = false;
      this->call_state_ = 2;  // dialing; verify promptly through CLCC
      this->start_call_list_();
      break;
    case STATE_ANSWER_WAIT:
      this->connect_pending_ = false;
      if (this->call_state_ != 0) {
        this->call_state_ = 0;
        this->call_connected_callback_.call();
      }
      this->finish_ready_();
      break;
    case STATE_HANGUP_WAIT:
      this->disconnect_pending_ = false;
      if (this->call_state_ != 6) {
        this->call_state_ = 6;
        this->incoming_call_notified_ = false;
        this->call_disconnected_callback_.call();
      }
      this->finish_ready_();
      break;
    case STATE_USSD_WAIT_CHARSET:
      this->send_command_("AT+CUSD=1,\"" + this->ussd_ + "\"", STATE_USSD_WAIT_ACK, 15000);
      break;
    case STATE_USSD_WAIT_ACK:
      this->ussd_pending_ = false;
      this->finish_ready_();
      break;
    default:
      ESP_LOGVV(TAG, "Unexpected OK in %s", this->state_name_(this->state_));
      break;
  }
}

void Sim800LComponent::process_error_(const std::string &line) {
  ESP_LOGW(TAG, "Modem error in %s: %s", this->state_name_(this->state_), line.c_str());
  this->publish_last_error_(line);

  switch (this->state_) {
    case STATE_WAIT_CMEE:
      // Detailed errors are useful but not required for basic SMS/call support.
      this->send_command_("AT+CUSD=1", STATE_WAIT_CUSD_ENABLE);
      return;
    case STATE_WAIT_CUSD_ENABLE:
      this->initialized_ = true;
      this->poll_requested_ = true;
      this->finish_ready_();
      return;
    case STATE_SMS_WAIT_CMGF:
    case STATE_SMS_WAIT_CHARSET:
    case STATE_SMS_WAIT_CSMP:
    case STATE_SMS_WAIT_PROMPT:
    case STATE_SMS_WAIT_RESULT:
    case STATE_SMS_WAIT_FINAL_OK:
      this->complete_active_sms_("failed", line);
      this->recover_("sms_command_error", false);
      return;
    case STATE_SMS_WAIT_RESTORE_CHARSET:
    case STATE_SMS_WAIT_RESTORE_CSMP:
      this->recover_("sms_restore_error", false);
      return;
    case STATE_DIAL_WAIT:
      this->dial_pending_ = false;
      this->finish_ready_();
      return;
    case STATE_ANSWER_WAIT:
      this->connect_pending_ = false;
      this->finish_ready_();
      return;
    case STATE_HANGUP_WAIT:
      this->disconnect_pending_ = false;
      this->finish_ready_();
      return;
    case STATE_USSD_WAIT_CHARSET:
    case STATE_USSD_WAIT_ACK:
      this->ussd_pending_ = false;
      this->finish_ready_();
      return;
    case STATE_WAIT_CREG:
    case STATE_WAIT_CSQ:
    case STATE_WAIT_SMS_DELETE:
    case STATE_WAIT_CALL_LIST:
      this->set_registered_(false);
      this->finish_ready_();
      return;
    default:
      this->recover_("modem_command_error", false);
      return;
  }
}

bool Sim800LComponent::process_urc_(const std::string &line) {
  if (line == "RDY" || line == "NORMAL POWER DOWN") {
    this->recover_(line == "RDY" ? "modem_restarted" : "modem_power_down", false);
    return true;
  }
  if (line == "SMS Ready") {
    this->sms_poll_requested_ = true;
    return true;
  }
  if (line == "Call Ready" || line == "PB DONE")
    return true;
  if (line.starts_with("+CMTI:")) {
    this->sms_poll_requested_ = true;
    return true;
  }
  if (line == "RING") {
    if (this->call_state_ != 4) {
      this->call_state_ = 4;
      this->incoming_call_notified_ = false;
    }
    return true;
  }
  if (line == "NO CARRIER" || line == "BUSY" || line == "NO ANSWER" || line == "NO DIALTONE") {
    const bool command_completed =
        this->state_ == STATE_DIAL_WAIT || this->state_ == STATE_ANSWER_WAIT || this->state_ == STATE_HANGUP_WAIT;
    if (this->state_ == STATE_DIAL_WAIT)
      this->dial_pending_ = false;
    else if (this->state_ == STATE_ANSWER_WAIT)
      this->connect_pending_ = false;
    else if (this->state_ == STATE_HANGUP_WAIT)
      this->disconnect_pending_ = false;
    if (this->call_state_ != 6) {
      this->call_state_ = 6;
      this->incoming_call_notified_ = false;
      this->call_disconnected_callback_.call();
    }
    if (command_completed)
      this->finish_ready_();
    return true;
  }
  if (line.starts_with("+CLIP:")) {
    this->parse_clip_(line);
    return true;
  }
  if (line.starts_with("+CUSD:")) {
    this->parse_ussd_(line);
    return true;
  }
  if (line.starts_with("+CREG:")) {
    this->parse_creg_(line);
    return true;
  }
  return false;
}

void Sim800LComponent::process_deadline_() {
  const uint32_t now = millis();
  if (!deadline_reached(now, this->state_deadline_ms_))
    return;
  this->state_deadline_ms_ = 0;
  if (this->state_ == STATE_BOOT_WAIT) {
    this->start_probe_();
    return;
  }
  this->recover_(std::string("timeout_") + this->state_name_(this->state_), true);
}

void Sim800LComponent::process_ready_() {
  if (this->state_ != STATE_READY || !this->initialized_)
    return;

  if (this->disconnect_pending_) {
    this->send_command_("ATH", STATE_HANGUP_WAIT);
    return;
  }
  if (this->connect_pending_) {
    this->send_command_("ATA", STATE_ANSWER_WAIT);
    return;
  }
  if (this->registered_ && this->sms_poll_requested_) {
    this->start_sms_list_();
    return;
  }
  if (this->registered_ && this->get_sms_queue_depth() > 0 && this->activate_next_sms_()) {
    this->start_active_sms_();
    return;
  }
  if (this->registered_ && this->dial_pending_) {
    this->send_command_("ATD" + this->dial_recipient_ + ';', STATE_DIAL_WAIT, 30000);
    return;
  }
  if (this->registered_ && this->ussd_pending_) {
    this->start_ussd_();
    return;
  }

  if (!this->registered_ &&
      (this->get_sms_queue_depth() > 0 || this->dial_pending_ || this->ussd_pending_))
    this->poll_requested_ = true;

  if (this->poll_requested_)
    this->start_regular_poll_();
}

void Sim800LComponent::start_probe_() { this->send_command_("AT", STATE_WAIT_AT); }

void Sim800LComponent::start_regular_poll_() {
  this->poll_requested_ = false;
  this->creg_seen_ = false;
  this->creg_registered_ = false;
  this->send_command_("AT+CREG?", STATE_WAIT_CREG);
}

void Sim800LComponent::start_sms_list_() {
  this->sms_poll_requested_ = false;
  this->reset_sms_list_parser_();
  this->send_command_("AT+CMGL=\"ALL\"", STATE_WAIT_SMS_LIST, 20000);
}

void Sim800LComponent::start_call_list_() {
  this->call_list_seen_ = false;
  this->send_command_("AT+CLCC", STATE_WAIT_CALL_LIST);
}

void Sim800LComponent::start_active_sms_() {
  if (!this->sms_active_)
    return;
  this->publish_sms_status_("sending");
  this->send_command_("AT+CMGF=1", STATE_SMS_WAIT_CMGF);
}

void Sim800LComponent::start_ussd_() {
  this->send_command_("AT+CSCS=\"GSM\"", STATE_USSD_WAIT_CHARSET);
}

void Sim800LComponent::finish_ready_() {
  this->last_command_.clear();
  this->set_state_(STATE_READY);
  this->process_ready_();
}

void Sim800LComponent::recover_(const std::string &reason, bool timeout) {
  const State failed_state = this->state_;
  ESP_LOGW(TAG, "Recovering modem state machine from %s", reason.c_str());

  if (timeout) {
    this->timeout_count_++;
  }
  this->recovery_count_++;
  this->publish_counters_();
  this->publish_last_error_(reason);

  if (failed_state == STATE_SMS_WAIT_PROMPT &&
      (timeout || reason == "uart_rx_line_overflow"))
    this->write_byte(ASCII_ESC);  // cancel text entry; Ctrl-Z would submit it

  if (this->sms_active_) {
    if (this->sms_body_submitted_) {
      this->complete_active_sms_("unknown", reason);
    } else if (this->active_sms_.attempts < SMS_PRE_SUBMIT_MAX_ATTEMPTS && this->requeue_active_sms_()) {
      ESP_LOGW(TAG, "Requeued SMS before body submission");
    } else {
      this->complete_active_sms_("failed", reason);
    }
  }

  const bool failed_probe = failed_state == STATE_WAIT_AT;
  const uint32_t retry_delay = failed_probe ? this->modem_retry_ms_ : BOOT_DELAY_MS;
  if (failed_probe)
    this->modem_retry_ms_ = std::min(this->modem_retry_ms_ * 2U, MODEM_RETRY_MAX_MS);

  this->initialized_ = false;
  this->set_registered_(false);
  this->last_command_.clear();
  this->read_pos_ = 0;
  this->discard_line_ = false;
  this->ignore_prompt_space_ = false;
  this->reset_sms_list_parser_();
  this->set_state_(STATE_BOOT_WAIT, retry_delay);
}

bool Sim800LComponent::enqueue_sms_(const std::string &recipient, const std::string &message, SmsEncoding encoding) {
  std::string error;
  if (!this->validate_sms_(recipient, message, encoding, &error)) {
    this->sms_failed_count_++;
    this->publish_sms_status_("failed");
    this->publish_last_error_(error);
    this->publish_counters_();
    return false;
  }
  if (this->get_sms_queue_depth() >= SIM800L_SMS_QUEUE_CAPACITY) {
    this->sms_failed_count_++;
    this->publish_sms_status_("failed");
    this->publish_last_error_("sms_queue_full");
    this->publish_counters_();
    return false;
  }

  const size_t tail = (this->sms_queue_head_ + this->sms_queue_size_) % SIM800L_SMS_QUEUE_CAPACITY;
  this->sms_queue_[tail] = SmsRequest{recipient, message, encoding, 0};
  this->sms_queue_size_++;
  this->publish_sms_status_("queued");
  this->publish_queue_depth_();
  if (this->state_ == STATE_READY)
    this->process_ready_();
  return true;
}

bool Sim800LComponent::activate_next_sms_() {
  if (this->sms_active_)
    return true;
  if (this->sms_queue_size_ == 0)
    return false;
  this->active_sms_ = std::move(this->sms_queue_[this->sms_queue_head_]);
  this->sms_queue_[this->sms_queue_head_] = SmsRequest{};
  this->sms_queue_head_ = (this->sms_queue_head_ + 1) % SIM800L_SMS_QUEUE_CAPACITY;
  this->sms_queue_size_--;
  this->active_sms_.attempts++;
  this->sms_active_ = true;
  this->sms_body_submitted_ = false;
  this->sms_reference_seen_ = false;
  this->publish_queue_depth_();
  return true;
}

bool Sim800LComponent::requeue_active_sms_() {
  if (!this->sms_active_ || this->sms_queue_size_ >= SIM800L_SMS_QUEUE_CAPACITY)
    return false;
  this->sms_queue_head_ = (this->sms_queue_head_ + SIM800L_SMS_QUEUE_CAPACITY - 1) % SIM800L_SMS_QUEUE_CAPACITY;
  this->sms_queue_[this->sms_queue_head_] = std::move(this->active_sms_);
  this->sms_queue_size_++;
  this->active_sms_ = SmsRequest{};
  this->sms_active_ = false;
  this->sms_body_submitted_ = false;
  this->sms_reference_seen_ = false;
  this->publish_sms_status_("queued");
  this->publish_queue_depth_();
  return true;
}

void Sim800LComponent::complete_active_sms_(const char *status, const std::string &error) {
  if (!this->sms_active_)
    return;
  if (std::strcmp(status, "sent") == 0) {
    this->sms_sent_count_++;
    this->publish_last_error_("none");
  } else if (std::strcmp(status, "unknown") == 0) {
    this->sms_unknown_count_++;
    this->publish_last_error_(error.empty() ? "sms_delivery_unknown" : error);
  } else {
    this->sms_failed_count_++;
    this->publish_last_error_(error.empty() ? "sms_failed" : error);
  }
  this->publish_sms_status_(status);
  this->active_sms_ = SmsRequest{};
  this->sms_active_ = false;
  this->sms_body_submitted_ = false;
  this->sms_reference_seen_ = false;
  this->publish_queue_depth_();
  this->publish_counters_();
}

void Sim800LComponent::publish_sms_status_(const std::string &status) {
  this->sms_status_ = status;
#ifdef USE_TEXT_SENSOR
  if (this->sms_status_text_sensor_ != nullptr &&
      (!this->sms_status_text_sensor_->has_state() || this->sms_status_text_sensor_->state != status))
    this->sms_status_text_sensor_->publish_state(status);
#endif
}

void Sim800LComponent::publish_last_error_(const std::string &error) {
  this->last_error_ = error.empty() ? "none" : error;
#ifdef USE_TEXT_SENSOR
  if (this->sms_last_error_text_sensor_ != nullptr &&
      (!this->sms_last_error_text_sensor_->has_state() || this->sms_last_error_text_sensor_->state != this->last_error_))
    this->sms_last_error_text_sensor_->publish_state(this->last_error_);
#endif
}

void Sim800LComponent::publish_queue_depth_() {
#ifdef USE_SENSOR
  if (this->sms_queue_depth_sensor_ != nullptr)
    this->sms_queue_depth_sensor_->publish_state(this->get_sms_queue_depth());
#endif
}

void Sim800LComponent::publish_counters_() {
#ifdef USE_SENSOR
  if (this->sms_sent_count_sensor_ != nullptr)
    this->sms_sent_count_sensor_->publish_state(this->sms_sent_count_);
  if (this->sms_failed_count_sensor_ != nullptr)
    this->sms_failed_count_sensor_->publish_state(this->sms_failed_count_);
  if (this->sms_unknown_count_sensor_ != nullptr)
    this->sms_unknown_count_sensor_->publish_state(this->sms_unknown_count_);
  if (this->sim800_timeout_count_sensor_ != nullptr)
    this->sim800_timeout_count_sensor_->publish_state(this->timeout_count_);
  if (this->sim800_recovery_count_sensor_ != nullptr)
    this->sim800_recovery_count_sensor_->publish_state(this->recovery_count_);
  if (this->uart_rx_overflow_count_sensor_ != nullptr)
    this->uart_rx_overflow_count_sensor_->publish_state(this->uart_rx_overflow_count_);
#endif
}

void Sim800LComponent::publish_last_response_age_() {
#ifdef USE_SENSOR
  if (this->sim800_last_response_age_sensor_ == nullptr)
    return;
  if (this->last_response_ms_ == 0) {
    this->sim800_last_response_age_sensor_->publish_state(NAN);
  } else {
    this->sim800_last_response_age_sensor_->publish_state(
        static_cast<float>(static_cast<uint32_t>(millis() - this->last_response_ms_)) / 1000.0f);
  }
#endif
}

bool Sim800LComponent::validate_sms_(const std::string &recipient, const std::string &message, SmsEncoding encoding,
                                     std::string *error) const {
  if (recipient.empty() || recipient.size() > SIM800L_MAX_RECIPIENT_LENGTH) {
    *error = "invalid_sms_recipient_length";
    return false;
  }
  if (message.empty()) {
    *error = "invalid_sms_message_length";
    return false;
  }
  if (encoding == SmsEncoding::UCS2) {
    if (!this->is_ucs2_hex_(recipient) || !this->is_ucs2_hex_(message)) {
      *error = "invalid_ucs2_hex";
      return false;
    }
    if (message.size() > SIM800L_MAX_UCS2_MESSAGE_HEX_LENGTH) {
      *error = "ucs2_sms_too_long";
      return false;
    }
    return true;
  }
  if (!std::all_of(recipient.begin(), recipient.end(), [](unsigned char value) {
        return std::isdigit(value) || value == '+';
      })) {
    *error = "invalid_sms_recipient";
    return false;
  }
  if (message.find(static_cast<char>(ASCII_CTRL_Z)) != std::string::npos ||
      message.find(static_cast<char>(ASCII_ESC)) != std::string::npos || message.find('\0') != std::string::npos) {
    *error = "invalid_sms_control_character";
    return false;
  }
  if (this->gsm_payload_units_(message) > SIM800L_MAX_GSM_SEPTETS) {
    *error = "gsm_sms_too_long";
    return false;
  }
  return true;
}

bool Sim800LComponent::validate_dial_string_(const std::string &value) const {
  return !value.empty() && value.size() <= SIM800L_MAX_RECIPIENT_LENGTH &&
         std::all_of(value.begin(), value.end(), [](unsigned char character) {
           return std::isdigit(character) || character == '+' || character == '*' || character == '#';
         });
}

bool Sim800LComponent::validate_ussd_(const std::string &value) const {
  return !value.empty() && value.size() <= 64 &&
         std::all_of(value.begin(), value.end(), [](unsigned char character) {
           return std::isdigit(character) || character == '+' || character == '*' || character == '#';
         });
}

size_t Sim800LComponent::gsm_payload_units_(const std::string &value) {
  size_t units = 0;
  for (const unsigned char character : value) {
    // GSM 03.38 extension-table characters need an escape septet. UTF-8 bytes
    // are counted conservatively one-by-one; the add-on normally selects UCS2
    // for text outside the GSM alphabet.
    switch (character) {
      case '^':
      case '{':
      case '}':
      case '\\':
      case '[':
      case '~':
      case ']':
      case '|':
      case '\f':
        units += 2;
        break;
      default:
        units++;
        break;
    }
    if (units > SIM800L_MAX_GSM_SEPTETS)
      return units;
  }
  return units;
}

bool Sim800LComponent::is_ucs2_hex_(const std::string &value) {
  return !value.empty() && value.size() % 4 == 0 &&
         std::all_of(value.begin(), value.end(), [](unsigned char character) { return std::isxdigit(character); });
}

void Sim800LComponent::reset_sms_list_parser_() {
  this->sms_list_index_ = 0;
  this->sms_delete_index_ = 0;
  this->sms_list_has_message_ = false;
  this->sms_list_ignore_rest_ = false;
  this->sms_sender_.clear();
  this->sms_message_.clear();
  this->sms_pending_line_.clear();
  this->sms_pending_line_valid_ = false;
}

bool Sim800LComponent::parse_sms_header_(const std::string &line) {
  const size_t colon = line.find(':');
  if (colon == std::string::npos)
    return false;
  const size_t first_comma = line.find(',', colon + 1);
  if (first_comma == std::string::npos)
    return false;
  const auto index = parse_number<uint16_t>(trim_copy(line.substr(colon + 1, first_comma - colon - 1)));
  if (!index.has_value() || *index == 0)
    return false;

  const size_t status_end = line.find(',', first_comma + 1);
  if (status_end == std::string::npos)
    return false;
  const size_t sender_start = line.find('"', status_end + 1);
  if (sender_start == std::string::npos)
    return false;
  const size_t sender_end = line.find('"', sender_start + 1);
  if (sender_end == std::string::npos)
    return false;

  this->sms_list_index_ = *index;
  this->sms_sender_ = line.substr(sender_start + 1, sender_end - sender_start - 1);
  this->sms_message_.clear();
  this->sms_pending_line_.clear();
  this->sms_pending_line_valid_ = false;
  this->sms_list_has_message_ = true;
  return true;
}

void Sim800LComponent::process_sms_list_line_(const std::string &line) {
  // The first line after +CMGL is always message data. This deliberately wins
  // over response-looking bodies such as "OK", "ERROR" or "+CMGL: ...".
  if (this->sms_list_has_message_ && !this->sms_pending_line_valid_ && !this->sms_list_ignore_rest_) {
    this->sms_pending_line_ = line;
    this->sms_pending_line_valid_ = true;
    return;
  }

  if (line.starts_with("+CMGL:")) {
    if (!this->sms_list_has_message_ && this->sms_delete_index_ == 0) {
      if (!this->parse_sms_header_(line)) {
        this->uart_rx_overflow_count_++;
        this->publish_counters_();
        this->publish_last_error_("invalid_cmgl_header");
      }
    } else if (!this->sms_list_ignore_rest_) {
      this->finish_received_sms_();
      this->sms_list_ignore_rest_ = true;
    }
    return;
  }

  if (line == "OK") {
    if (this->sms_list_has_message_)
      this->finish_received_sms_();
    if (this->sms_delete_index_ != 0) {
      this->send_command_("AT+CMGD=" + std::to_string(this->sms_delete_index_), STATE_WAIT_SMS_DELETE);
    } else {
      this->start_call_list_();
    }
    return;
  }

  // ERROR/CME/CMS is a command failure only before a CMGL header. Once a
  // message body is active the same text is valid user content.
  if (is_modem_error(line) && !this->sms_list_has_message_) {
    this->process_error_(line);
    return;
  }
  if (this->sms_list_ignore_rest_ || !this->sms_list_has_message_)
    return;

  if (this->sms_pending_line_valid_) {
    if (!this->sms_message_.empty())
      this->sms_message_ += '\n';
    this->sms_message_ += this->sms_pending_line_;
  }
  this->sms_pending_line_ = line;
  this->sms_pending_line_valid_ = true;
}

void Sim800LComponent::finish_received_sms_() {
  if (!this->sms_list_has_message_)
    return;
  if (this->sms_pending_line_valid_) {
    if (!this->sms_message_.empty())
      this->sms_message_ += '\n';
    this->sms_message_ += this->sms_pending_line_;
  }
  ESP_LOGD(TAG, "Received SMS from %s (%u chars)", this->sms_sender_.c_str(),
           static_cast<unsigned>(this->sms_message_.size()));
  this->sms_received_callback_.call(this->sms_message_, this->sms_sender_);
  this->sms_delete_index_ = this->sms_list_index_;
  this->sms_list_has_message_ = false;
  this->sms_pending_line_.clear();
  this->sms_pending_line_valid_ = false;
  this->sms_sender_.clear();
  this->sms_message_.clear();
}

void Sim800LComponent::parse_creg_(const std::string &line) {
  const size_t colon = line.find(':');
  if (colon == std::string::npos)
    return;
  std::string payload = trim_copy(line.substr(colon + 1));
  const size_t first_comma = payload.find(',');
  const std::string first_field = trim_copy(
      first_comma == std::string::npos ? payload : payload.substr(0, first_comma));
  auto status = parse_number<int>(first_field);
  if (first_comma != std::string::npos) {
    const size_t second_comma = payload.find(',', first_comma + 1);
    const std::string second_field = trim_copy(payload.substr(
        first_comma + 1,
        second_comma == std::string::npos ? std::string::npos : second_comma - first_comma - 1));
    // Query replies contain <n>,<stat>[,...]. Extended unsolicited replies
    // contain <stat>,"<lac>",...; in that form the second field is not numeric.
    if (const auto query_status = parse_number<int>(second_field); query_status.has_value())
      status = query_status;
  }
  if (!status.has_value())
    return;
  const bool registered = *status == 1 || *status == 5;
  if (this->state_ == STATE_WAIT_CREG) {
    this->creg_seen_ = true;
    this->creg_registered_ = registered;
  } else {
    this->set_registered_(registered);
  }
}

void Sim800LComponent::parse_csq_(const std::string &line) {
  const size_t colon = line.find(':');
  const size_t comma = line.find(',', colon == std::string::npos ? 0 : colon + 1);
  if (colon == std::string::npos || comma == std::string::npos)
    return;
  const auto raw = parse_number<int>(trim_copy(line.substr(colon + 1, comma - colon - 1)));
  if (!raw.has_value())
    return;
#ifdef USE_SENSOR
  if (this->rssi_sensor_ != nullptr) {
    if (*raw >= 0 && *raw <= 31)
      this->rssi_sensor_->publish_state(-113 + 2 * *raw);
    else
      this->rssi_sensor_->publish_state(NAN);  // CSQ 99 means unknown
  }
#endif
}

void Sim800LComponent::parse_clcc_(const std::string &line) {
  const size_t colon = line.find(':');
  if (colon == std::string::npos)
    return;
  size_t start = colon + 1;
  for (uint8_t field = 0; field <= 2; field++) {
    const size_t end = line.find(',', start);
    if (end == std::string::npos)
      return;
    if (field == 2) {
      const auto status = parse_number<uint8_t>(trim_copy(line.substr(start, end - start)));
      if (!status.has_value())
        return;
      this->call_list_seen_ = true;
      if (*status != this->call_state_) {
        this->call_state_ = *status;
        if (*status == 0)
          this->call_connected_callback_.call();
      }
      return;
    }
    start = end + 1;
  }
}

void Sim800LComponent::parse_clip_(const std::string &line) {
  const size_t colon = line.find(':');
  const size_t first_quote = line.find('"', colon == std::string::npos ? 0 : colon + 1);
  const size_t second_quote = line.find('"', first_quote == std::string::npos ? 0 : first_quote + 1);
  if (first_quote == std::string::npos || second_quote == std::string::npos)
    return;
  const std::string caller = line.substr(first_quote + 1, second_quote - first_quote - 1);
  this->call_state_ = 4;
  if (!this->incoming_call_notified_) {
    this->incoming_call_notified_ = true;
    this->incoming_call_callback_.call(caller);
  }
}

void Sim800LComponent::parse_ussd_(const std::string &line) {
  const size_t first_quote = line.find('"');
  const size_t second_quote = line.find('"', first_quote == std::string::npos ? 0 : first_quote + 1);
  if (first_quote == std::string::npos || second_quote == std::string::npos)
    return;
  this->ussd_received_callback_.call(line.substr(first_quote + 1, second_quote - first_quote - 1));
}

void Sim800LComponent::set_registered_(bool registered) {
  const bool changed = this->registered_ != registered;
  this->registered_ = registered;
#ifdef USE_BINARY_SENSOR
  if (this->registered_binary_sensor_ != nullptr &&
      (changed || !this->registered_binary_sensor_->has_state()))
    this->registered_binary_sensor_->publish_state(registered);
#endif
}

void Sim800LComponent::send_sms(const std::string &recipient, const std::string &message) {
  this->enqueue_sms_(recipient, message, SmsEncoding::GSM);
}

void Sim800LComponent::send_sms_unicode(const std::string &recipient, const std::string &message) {
  this->enqueue_sms_(recipient, message, SmsEncoding::UCS2);
}

void Sim800LComponent::send_ussd(const std::string &ussd_code) {
  if (!this->validate_ussd_(ussd_code)) {
    this->publish_last_error_("invalid_ussd");
    return;
  }
  if (this->ussd_pending_) {
    this->publish_last_error_("ussd_busy");
    return;
  }
  this->ussd_ = ussd_code;
  this->ussd_pending_ = true;
  if (this->state_ == STATE_READY)
    this->process_ready_();
}

void Sim800LComponent::dial(const std::string &recipient) {
  if (!this->validate_dial_string_(recipient)) {
    this->publish_last_error_("invalid_dial_recipient");
    return;
  }
  if (this->dial_pending_) {
    this->publish_last_error_("dial_busy");
    return;
  }
  this->dial_recipient_ = recipient;
  this->dial_pending_ = true;
  if (this->state_ == STATE_READY)
    this->process_ready_();
}

void Sim800LComponent::connect() {
  this->connect_pending_ = true;
  if (this->state_ == STATE_READY)
    this->process_ready_();
}

void Sim800LComponent::disconnect() {
  this->disconnect_pending_ = true;
  if (this->state_ == STATE_DIAL_WAIT || this->state_ == STATE_ANSWER_WAIT) {
    // Voice dialing/answering may otherwise hold the command state for up to
    // 30 seconds. ATH is a safe call abort, but never preempt an SMS body or
    // its result because that could create a duplicate/unknown submission.
    this->dial_pending_ = false;
    this->connect_pending_ = false;
    this->send_command_("ATH", STATE_HANGUP_WAIT);
  } else if (this->state_ == STATE_READY) {
    this->process_ready_();
  }
}

void Sim800LComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "SIM800L:");
  ESP_LOGCONFIG(TAG, "  Q-Tronic serialized transport: enabled");
  ESP_LOGCONFIG(TAG, "  SMS queue capacity: %u", static_cast<unsigned>(SIM800L_SMS_QUEUE_CAPACITY));
  ESP_LOGCONFIG(TAG, "  SMS result timeout: %u ms", static_cast<unsigned>(SMS_RESULT_TIMEOUT_MS));
#ifdef USE_BINARY_SENSOR
  LOG_BINARY_SENSOR("  ", "Registered", this->registered_binary_sensor_);
#endif
#ifdef USE_SENSOR
  LOG_SENSOR("  ", "RSSI", this->rssi_sensor_);
#endif
}

}  // namespace esphome::sim800l
