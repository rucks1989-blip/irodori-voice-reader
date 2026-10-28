#include "engine/framework/runtime/registry.h"
#include "engine/framework/debug/trace.h"
#include "engine/framework/assets/model_package.h"
#include "engine/framework/io/filesystem.h"

#include <algorithm>
#include <stdexcept>

namespace engine::runtime {

namespace {

void log_model_load_trace(const ModelInspection & inspection, const ILoadedVoiceModel & model) {
    if (!engine::debug::trace_log_enabled()) {
        return;
    }
    const auto & metadata = model.metadata();
    engine::debug::trace_log_scalar("runtime.model.family", metadata.family);
    engine::debug::trace_log_scalar("runtime.model.variant", metadata.variant);
    engine::debug::trace_log_scalar("runtime.model.root", inspection.model_root.string());
    engine::debug::trace_log_scalar("runtime.model.discovered_config_count", inspection.discovered_configs.size());
    engine::debug::trace_log_scalar("runtime.model.discovered_weight_count", inspection.discovered_weights.size());
    engine::debug::trace_log_scalar("runtime.model.task_count", model.capabilities().supported_tasks.size());
    engine::debug::trace_log_scalar("runtime.model.language_count", model.capabilities().languages.size());
    engine::debug::trace_log_scalar("runtime.model.supports_speaker_reference", model.capabilities().supports_speaker_reference);
    engine::debug::trace_log_scalar("runtime.model.supports_style_condition", model.capabilities().supports_style_condition);
    engine::debug::trace_log_scalar("runtime.model.supports_timestamps", model.capabilities().supports_timestamps);
}

}  // namespace

void ModelRegistry::register_loader(std::shared_ptr<IVoiceModelLoader> loader) {
    if (loader == nullptr) {
        throw std::invalid_argument("model loader must not be null");
    }
    loaders_.push_back(std::move(loader));
}

bool ModelRegistry::empty() const noexcept {
    return loaders_.empty();
}

size_t ModelRegistry::size() const noexcept {
    return loaders_.size();
}

std::vector<std::string> ModelRegistry::families() const {
    std::vector<std::string> names;
    names.reserve(loaders_.size());
    for (const auto & loader : loaders_) {
        names.push_back(loader->family());
    }
    std::sort(names.begin(), names.end());
    names.erase(std::unique(names.begin(), names.end()), names.end());
    return names;
}

bool ModelRegistry::supports_family(const std::string & family) const noexcept {
    for (const auto & loader : loaders_) {
        if (loader->family() == family) {
            return true;
        }
    }
    return false;
}

ModelInspection ModelRegistry::inspect(const ModelLoadRequest & request) const {
    engine::assets::ScopedModelPackageSpecOverride spec_override(request.model_spec_override, request.model_path);
    validate_request(request);
    const auto * loader = find_loader(request);
    if (loader == nullptr) {
        throw std::runtime_error("no registered model loader can inspect: " + request.model_path.string());
    }
    return loader->inspect(request);
}

ModelInspection ModelRegistry::inspect(const std::filesystem::path & model_path) const {
    ModelLoadRequest request;
    request.model_path = model_path;
    return inspect(request);
}

std::unique_ptr<ILoadedVoiceModel> ModelRegistry::load(const ModelLoadRequest & request) const {
    engine::assets::ScopedModelPackageSpecOverride spec_override(request.model_spec_override, request.model_path);
    validate_request(request);
    const auto * loader = find_loader(request);
    if (loader == nullptr) {
        throw std::runtime_error("no registered model loader can load: " + request.model_path.string());
    }
    const auto inspection = engine::debug::trace_log_enabled()
        ? std::optional<ModelInspection>(loader->inspect(request))
        : std::nullopt;
    auto model = loader->load(request);
    if (inspection.has_value()) {
        log_model_load_trace(*inspection, *model);
    }
    return model;
}

std::unique_ptr<ILoadedVoiceModel> ModelRegistry::load(const std::filesystem::path & model_path) const {
    ModelLoadRequest request;
    request.model_path = model_path;
    return load(request);
}

void ModelRegistry::validate_request(const ModelLoadRequest & request) const {
    if (!engine::io::is_existing_file(request.model_path) && !engine::io::is_existing_directory(request.model_path)) {
        throw std::runtime_error("model path does not exist: " + request.model_path.string());
    }
    if (request.family_hint.has_value() && !supports_family(*request.family_hint)) {
        throw std::runtime_error("unsupported model family hint: " + *request.family_hint);
    }
    if (request.model_spec_override.has_value() &&
        !engine::io::is_existing_file(*request.model_spec_override) &&
        !engine::io::is_existing_directory(*request.model_spec_override)) {
        throw std::runtime_error("model package spec override path does not exist: " + request.model_spec_override->string());
    }
}

const IVoiceModelLoader * ModelRegistry::find_loader(const ModelLoadRequest & request) const {
    for (const auto & loader : loaders_) {
        if (request.family_hint.has_value() && loader->family() != *request.family_hint) {
            continue;
        }
        if (request.family_hint.has_value()) {
            return loader.get();
        }
        if (loader->can_load(request)) {
            return loader.get();
        }
    }
    return nullptr;
}

}  // namespace engine::runtime
