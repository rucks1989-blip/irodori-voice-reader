namespace engine::runtime { class ModelRegistry; ModelRegistry make_irodori_invest_registry(); }
#include "engine/framework/audio/wav_reader.h"
#include "engine/framework/io/safetensors.h"
#include "engine/framework/runtime/registry.h"
#include "engine/framework/runtime/session.h"
#include "engine/models/irodori_tts/session.h"

#include <cstring>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

std::string arg_value(int argc, char ** argv, const std::string & name, const std::string & fallback) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (argv[i] == name) {
            return argv[i + 1];
        }
    }
    return fallback;
}

int int_arg(int argc, char ** argv, const std::string & name, int fallback) {
    return std::stoi(arg_value(argc, argv, name, std::to_string(fallback)));
}

engine::core::BackendType parse_backend(const std::string & value) {
    if (value == "cuda") {
        return engine::core::BackendType::Cuda;
    }
    throw std::runtime_error("speaker_embedding_dump supports only the cuda backend");
}

engine::runtime::AudioBuffer read_audio_buffer(const std::filesystem::path & path) {
    const auto wav = engine::audio::read_wav_f32(path);
    return engine::runtime::AudioBuffer{wav.sample_rate, wav.channels, wav.samples};
}

}  // namespace

int main(int argc, char ** argv) {
    try {
        const std::filesystem::path model_path = arg_value(argc, argv, "--model", "models/Irodori-TTS-500M-v3");
        const std::string backend_name = arg_value(argc, argv, "--backend", "cuda");
        const int device = int_arg(argc, argv, "--device", 0);
        const int threads = int_arg(argc, argv, "--threads", 8);
        const std::filesystem::path wav_path = arg_value(argc, argv, "--wav", "");
        const std::filesystem::path out_path = arg_value(argc, argv, "--out", "speaker.safetensors");
        const int tokens_target = int_arg(argc, argv, "--tokens", 0);
        if (wav_path.empty() || !std::filesystem::exists(wav_path)) {
            throw std::runtime_error("usage: speaker_embedding_dump --model <dir|gguf> --wav <reference.wav> --out <speaker.safetensors> [--tokens 16] [--device 0] [--threads 8]");
        }

        engine::runtime::ModelLoadRequest load_request;
        load_request.model_path = model_path;
        load_request.family_hint = "irodori_tts";

        auto registry = engine::runtime::make_irodori_invest_registry();
        auto model = registry.load(load_request);

        engine::runtime::SessionOptions options;
        options.backend.type = parse_backend(backend_name);
        options.backend.device = device;
        options.backend.threads = threads;

        auto session_base = model->create_task_session(
            {engine::runtime::VoiceTaskKind::Tts, engine::runtime::RunMode::Offline},
            options);
        auto * session = dynamic_cast<engine::models::irodori_tts::IrodoriTTSSession *>(session_base.get());
        if (session == nullptr) {
            throw std::runtime_error("loaded Irodori-TTS session is not the expected type");
        }

        const auto audio = read_audio_buffer(wav_path);
        const auto cond = session->extract_speaker_condition(audio);
        if (!cond.has_speaker || cond.tokens <= 0 || cond.state.empty() ||
            cond.state.size() % static_cast<size_t>(cond.tokens) != 0) {
            throw std::runtime_error("extracted speaker condition is invalid");
        }
        const int64_t dim = static_cast<int64_t>(cond.state.size() / static_cast<size_t>(cond.tokens));

        std::vector<float> state = cond.state;
        int64_t tokens = cond.tokens;
        if (tokens_target > 0 && tokens_target < tokens) {
            // Compress a variable-length speaker condition down to a fixed
            // token count. The first token produced by the speaker encoder is
            // the masked-mean summary of the whole reference (see
            // prepend_masked_mean_token); it is kept as-is. The remaining
            // time-step tokens are subsampled evenly so that the fixed-length
            // profile retains a representative spread of the reference.
            if (tokens_target < 1) {
                throw std::runtime_error("--tokens must be at least 1");
            }
            const int64_t source_time = tokens - 1;
            const int64_t target_time = tokens_target - 1;
            std::vector<float> compressed(
                static_cast<size_t>(tokens_target * dim), 0.0F);
            std::copy(state.begin(), state.begin() + dim, compressed.begin());
            for (int64_t i = 0; i < target_time; ++i) {
                const int64_t src =
                    source_time <= 0 ? 0
                                     : (i * source_time + (target_time - 1) / 2) /
                                           target_time;
                std::copy(state.begin() + (src + 1) * dim,
                          state.begin() + (src + 2) * dim,
                          compressed.begin() + (i + 1) * dim);
            }
            state.swap(compressed);
            tokens = tokens_target;
        }

        engine::io::SafeTensorWriteEntry entry;
        entry.name = "speaker_embedding";
        entry.dtype = "F32";
        entry.shape = {tokens, dim};
        entry.data.resize(state.size() * sizeof(float));
        std::memcpy(entry.data.data(), state.data(), entry.data.size());

        engine::io::write_safetensors_file(out_path, {entry});
        std::cout << "wrote " << out_path.string() << " tensor=speaker_embedding dtype=F32 shape=["
                  << tokens << "," << dim << "]" << std::endl;
        return 0;
    } catch (const std::exception & ex) {
        std::cerr << "speaker_embedding_dump failed: " << ex.what() << "\n";
        return 1;
    }
}