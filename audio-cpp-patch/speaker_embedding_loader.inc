uint64_t read_u64_le(const std::array<uint8_t, 8> &bytes) {
  uint64_t value = 0;
  for (size_t i = 0; i < bytes.size(); ++i) value |= static_cast<uint64_t>(bytes[i]) << (i * 8);
  return value;
}

std::vector<wchar_t> utf8_path_to_wide(const std::string &raw_path) {
  const int length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
      raw_path.data(), static_cast<int>(raw_path.size()), nullptr, 0);
  if (length <= 0) throw std::runtime_error("speaker_embedding_path is not valid UTF-8");
  std::vector<wchar_t> path(static_cast<size_t>(length) + 1, L'\0');
  if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, raw_path.data(),
      static_cast<int>(raw_path.size()), path.data(), length) != length) {
    throw std::runtime_error("speaker_embedding_path UTF-8 conversion failed");
  }
  return path;
}

std::vector<uint8_t> read_speaker_embedding_file(const std::string &raw_path) {
  const auto path = utf8_path_to_wide(raw_path);
  HANDLE handle = CreateFileW(path.data(), GENERIC_READ, FILE_SHARE_READ, nullptr,
      OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (handle == INVALID_HANDLE_VALUE)
    throw std::runtime_error("speaker_embedding_path cannot be opened: " + raw_path);
  LARGE_INTEGER size{};
  if (!GetFileSizeEx(handle, &size) || size.QuadPart < 0 ||
      static_cast<unsigned long long>(size.QuadPart) >
          static_cast<unsigned long long>(std::numeric_limits<size_t>::max())) {
    CloseHandle(handle);
    throw std::runtime_error("speaker embedding file size is invalid");
  }
  std::vector<uint8_t> data(static_cast<size_t>(size.QuadPart));
  size_t offset = 0;
  while (offset < data.size()) {
    const size_t remaining = data.size() - offset;
    const DWORD request = static_cast<DWORD>(std::min(
        remaining, static_cast<size_t>(std::numeric_limits<DWORD>::max())));
    DWORD read = 0;
    if (!ReadFile(handle, data.data() + offset, request, &read, nullptr) || read == 0) {
      CloseHandle(handle);
      throw std::runtime_error("speaker embedding file read failed");
    }
    offset += read;
  }
  CloseHandle(handle);
  return data;
}

IrodoriSpeakerCondition load_speaker_embedding(
    const std::string &raw_path, const IrodoriModelConfig &config) {
  const auto file_data = read_speaker_embedding_file(raw_path);
  if (file_data.size() < 8) throw std::runtime_error("speaker embedding safetensors header length is truncated");
  std::array<uint8_t, 8> length_bytes{};
  std::memcpy(length_bytes.data(), file_data.data(), length_bytes.size());
  const uint64_t header_size = read_u64_le(length_bytes);
  constexpr uint64_t kMaxHeaderBytes = 64ull * 1024ull * 1024ull;
  if (header_size == 0 || header_size > kMaxHeaderBytes)
    throw std::runtime_error("speaker embedding safetensors header length is invalid");
  if (header_size > file_data.size() - 8)
    throw std::runtime_error("speaker embedding safetensors header is truncated");
  const std::string header(reinterpret_cast<const char *>(file_data.data() + 8),
      static_cast<size_t>(header_size));
  const auto root = engine::io::json::parse(header);
  const auto &tensor = root.require("speaker_embedding");
  const auto dtype = engine::io::json::require_string(tensor, "dtype");
  if (dtype != "F32") throw std::runtime_error("speaker_embedding dtype must be F32, got " + dtype);
  const auto shape = tensor.require("shape").as_array();
  if (shape.size() != 2 || shape[0].as_i64() != 16 || shape[1].as_i64() != config.speaker_dim)
    throw std::runtime_error("speaker_embedding shape must be [16,speaker_dim]");
  const auto offsets = tensor.require("data_offsets").as_array();
  if (offsets.size() != 2) throw std::runtime_error("speaker_embedding data_offsets must contain two entries");
  const uint64_t data_begin = static_cast<uint64_t>(offsets[0].as_i64());
  const uint64_t data_end = static_cast<uint64_t>(offsets[1].as_i64());
  constexpr uint64_t kTokens = 16;
  const uint64_t expected_bytes = kTokens * static_cast<uint64_t>(config.speaker_dim) * sizeof(float);
  if (data_end < data_begin || data_end - data_begin != expected_bytes)
    throw std::runtime_error("speaker_embedding data size does not match [16,speaker_dim] F32");
  const uint64_t payload_start = 8ull + header_size;
  if (payload_start > file_data.size() || data_end > file_data.size() - payload_start)
    throw std::runtime_error("speaker_embedding data_offsets exceed file size");
  const auto *bytes = file_data.data() + static_cast<size_t>(payload_start + data_begin);
  IrodoriSpeakerCondition out;
  out.tokens = static_cast<int64_t>(kTokens);
  out.state.resize(static_cast<size_t>(expected_bytes / sizeof(float)));
  for (size_t i = 0; i < out.state.size(); ++i) {
    const uint32_t bits = static_cast<uint32_t>(bytes[i * 4]) |
        (static_cast<uint32_t>(bytes[i * 4 + 1]) << 8) |
        (static_cast<uint32_t>(bytes[i * 4 + 2]) << 16) |
        (static_cast<uint32_t>(bytes[i * 4 + 3]) << 24);
    std::memcpy(&out.state[i], &bits, sizeof(float));
    if (!std::isfinite(out.state[i])) throw std::runtime_error("speaker_embedding contains NaN or infinity");
  }
  out.mask.assign(static_cast<size_t>(out.tokens), 1);
  out.has_speaker = true;
  debug::trace_log_scalar("irodori_tts.speaker_embedding_path", raw_path);
  debug::trace_log_scalar("irodori_tts.speaker_embedding.dtype", std::string("F32"));
  debug::trace_log_scalar("irodori_tts.speaker_embedding.tokens", out.tokens);
  debug::trace_log_scalar("irodori_tts.speaker_embedding.dim", config.speaker_dim);
  debug::trace_log_scalar("irodori_tts.speaker_embedding.direct", true);
  return out;
}
