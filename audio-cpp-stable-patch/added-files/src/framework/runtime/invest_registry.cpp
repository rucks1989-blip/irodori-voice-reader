#include "engine/framework/runtime/registry.h"
#include "engine/models/irodori_tts/loader.h"

namespace engine::runtime {

ModelRegistry make_irodori_invest_registry() {
    ModelRegistry registry;
    registry.register_loader(engine::models::irodori_tts::make_irodori_tts_loader());
    return registry;
}

}  // namespace engine::runtime
