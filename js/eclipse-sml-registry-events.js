export const SML_REGISTRY_CHANGED_EVENT = 'eclipse:sml-registry-changed';

export function emitSmlRegistryChanged() {
    window.dispatchEvent(new CustomEvent(SML_REGISTRY_CHANGED_EVENT));
}

export function onSmlRegistryChanged(listener) {
    window.addEventListener(SML_REGISTRY_CHANGED_EVENT, listener);
    return () => window.removeEventListener(SML_REGISTRY_CHANGED_EVENT, listener);
}
