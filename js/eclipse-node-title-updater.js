import { app, api } from './comfy/index.js';

app.registerExtension({
    name: "Eclipse.NodeTitleUpdater",
    async setup(app) {
        api.addEventListener("eclipse/update_node_title", (e) => {
            const detail = e.detail;
            if (!detail) return;
            const nodeId = detail.node_id;
            const title = detail.title;
            const node = app.graph.getNodeById(nodeId);
            if (node) {
                node.title = title;
                app.canvas.draw(true, true);
            }
        });
    }
});
