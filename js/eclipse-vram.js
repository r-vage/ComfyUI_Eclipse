import {
    app,
    api
} from './comfy/index.js';
app.registerExtension({
    name: 'memory.cleanup',
    init() {
        api.addEventListener('memory_cleanup', ({
            detail
        }) => {
            if (detail.type !== 'cleanup_request') return;
            fetch('/free', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(detail.data),
            }).then((resp) => {
                if (!resp.ok) console.error('Memory cleanup request failed');
            }).catch((err) => {
                console.error('Error sending memory cleanup request:', err);
            });
        });
    },
});
