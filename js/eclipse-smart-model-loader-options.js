const STANDARD_MODEL_PRECISIONS = Object.freeze([
    'default',
    'fp32',
    'fp16',
    'bf16',
    'mxfp8',
    'fp8_mixed',
    'fp8_scaled',
    'fp8',
    'int8',
    'nf4',
    'nvfp4',
    'int4',
    'fp8_e4m3fn',
]);

const GGUF_QUANTIZATIONS = Object.freeze([
    'Q8_0', 'Q6_K', 'Q5_K_M', 'Q5_K_S', 'Q5_1', 'Q5_0',
    'Q4_K_XL', 'Q4_K_M', 'Q4_K_S', 'Q4_1', 'Q4_0',
    'Q3_K_XL', 'Q3_K_L', 'Q3_K_M', 'Q3_K_S',
    'Q2_K_XL', 'Q2_K', 'Q2_K_S',
    'IQ4_XS', 'IQ4_KS', 'IQ4_NL',
    'IQ3_M', 'IQ3_S', 'IQ3_XS', 'IQ3_XXS',
    'IQ2_XS', 'IQ2_XXS', 'IQ2_S', 'IQ2_M',
    'IQ1_S', 'IQ1_M', 'TQ2_0', 'TQ1_0',
]);

const GGUF_MODEL_PRECISIONS = Object.freeze([
    'default',
    'gguf',
    'gguf_unquantized',
    ...GGUF_QUANTIZATIONS,
]);

export function getModelPrecisionOptions(modelType) {
    return modelType === 'GGUF Model'
        ? [...GGUF_MODEL_PRECISIONS]
        : [...STANDARD_MODEL_PRECISIONS];
}

export function getDownloadPhaseLabel(phase, percent = 0) {
    const boundedPercent = Math.max(0, Math.min(100, Math.trunc(Number(percent) || 0)));
    if (phase === 'hashing') return `… Hashing · ${boundedPercent}%`;
    if (phase === 'verifying') return `… Verifying · ${boundedPercent}%`;
    const labels = {
        resolving: '… Resolving',
        locking: '… Waiting for idle queue',
        promoting: '… Promoting',
        completed: '✓ Downloaded',
        failed: '✗ Download failed',
        aborted: '■ Download aborted',
    };
    return labels[phase] || '… Processing';
}

export function reconcileFilenameFreeLocators(targetRole, value) {
    const role = typeof targetRole === 'string' ? targetRole.trim() : '';
    const locator = typeof value === 'string' ? value.trim() : '';
    if (!role || !locator) return [];
    return [{
        target_role: role,
        ...(locator.toLowerCase().startsWith('urn:air:')
            ? { air: locator }
            : { sha256: locator }),
    }];
}

export function consumeDownloadLocator(locators, targetRole, air, sha256) {
    if (!Array.isArray(locators)) return [];
    let consumed = false;
    return locators.filter((item) => {
        if (consumed || !item || item.target_role !== targetRole) return true;
        const identityMatches = (air && item.air === air) || (sha256 && item.sha256 === sha256);
        if (!identityMatches) return true;
        consumed = true;
        return false;
    });
}

export { GGUF_MODEL_PRECISIONS, GGUF_QUANTIZATIONS, STANDARD_MODEL_PRECISIONS };
