#include "engine/models/irodori_tts/audio_quality.h"

#include <cassert>
#include <cmath>
#include <limits>
#include <vector>

using engine::models::irodori_tts::analyze_irodori_audio_quality;
using engine::models::irodori_tts::irodori_audio_quality_failed;

int main() {
  {
    const auto metrics =
        analyze_irodori_audio_quality(std::vector<float>{});
    assert(metrics.max_adjacent_jump == 0.0);
    assert(metrics.non_finite_samples == 0);
    assert(!irodori_audio_quality_failed(metrics, 0.4F));
  }
  {
    const auto metrics = analyze_irodori_audio_quality(
        std::vector<float>{-0.1F, 0.2F, 0.55F});
    assert(std::abs(metrics.max_adjacent_jump - 0.35) < 1.0e-6);
    assert(metrics.non_finite_samples == 0);
    assert(!irodori_audio_quality_failed(metrics, 0.4F));
  }
  {
    const auto metrics =
        analyze_irodori_audio_quality(std::vector<float>{0.0F, 0.41F});
    assert(irodori_audio_quality_failed(metrics, 0.4F));
  }
  {
    const auto metrics = analyze_irodori_audio_quality(std::vector<float>{
        0.0F, std::numeric_limits<float>::quiet_NaN(),
        std::numeric_limits<float>::infinity(), 0.0F});
    assert(metrics.non_finite_samples == 2);
    assert(irodori_audio_quality_failed(metrics, 0.4F));
  }
  return 0;
}
