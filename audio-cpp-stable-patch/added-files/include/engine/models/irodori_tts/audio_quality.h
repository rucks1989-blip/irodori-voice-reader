#pragma once

#include <cstddef>
#include <vector>

namespace engine::models::irodori_tts {

struct IrodoriAudioQualityMetrics {
  double max_adjacent_jump = 0.0;
  size_t non_finite_samples = 0;
};

IrodoriAudioQualityMetrics
analyze_irodori_audio_quality(const std::vector<float> &samples);

bool irodori_audio_quality_failed(const IrodoriAudioQualityMetrics &metrics,
                                  float max_adjacent_jump);

} // namespace engine::models::irodori_tts
