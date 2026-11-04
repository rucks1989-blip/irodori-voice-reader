#include "engine/models/irodori_tts/audio_quality.h"

#include <algorithm>
#include <cmath>

namespace engine::models::irodori_tts {

IrodoriAudioQualityMetrics
analyze_irodori_audio_quality(const std::vector<float> &samples) {
  IrodoriAudioQualityMetrics metrics;
  for (size_t index = 0; index < samples.size(); ++index) {
    const float sample = samples[index];
    if (!std::isfinite(sample)) {
      ++metrics.non_finite_samples;
      continue;
    }
    if (index == 0 || !std::isfinite(samples[index - 1])) {
      continue;
    }
    metrics.max_adjacent_jump =
        std::max(metrics.max_adjacent_jump,
                 std::abs(static_cast<double>(sample) - samples[index - 1]));
  }
  return metrics;
}

bool irodori_audio_quality_failed(const IrodoriAudioQualityMetrics &metrics,
                                  float max_adjacent_jump) {
  return metrics.non_finite_samples != 0 ||
         metrics.max_adjacent_jump > static_cast<double>(max_adjacent_jump);
}

} // namespace engine::models::irodori_tts
