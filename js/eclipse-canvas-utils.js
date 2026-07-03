import {
    app
} from './comfy/index.js';
const canvasUtilsName = 'Eclipse.canvasUtils';
let newAPIAvailable = !1;

function getMenuItems() {
    return [null, {
        content: 'Arrange (vertical)',
        callback: () => app.graph.arrange(4 * LiteGraph.CANVAS_GRID_SIZE, LiteGraph.VERTICAL_LAYOUT),
    }, {
        content: 'Arrange (horizontal)',
        callback: () => app.graph.arrange(2 * LiteGraph.CANVAS_GRID_SIZE)
    }, null, {
        content: 'Pin all Nodes',
        callback: () => {
            app.graph._nodes.forEach((a) => {
                a.flags.pinned = !0;
            });
        },
    }, {
        content: 'Unpin all Nodes',
        callback: () => {
            app.graph._nodes.forEach((a) => {
                a.flags.pinned = !1;
            });
        },
    }, ];
}
app.registerExtension({
    name: canvasUtilsName,
    getCanvasMenuItems: (a) => ((newAPIAvailable = !0), getMenuItems()),
    async setup(a) {
        const n = LGraphCanvas.prototype.onContextMenu;
        let e = !1;
        LGraphCanvas.prototype.onContextMenu = function (a, t) {
            if (((LGraphCanvas.prototype.onContextMenu = n), !newAPIAvailable && !e)) {
                e = !0;
                const a = LGraphCanvas.prototype.getCanvasMenuOptions;
                LGraphCanvas.prototype.getCanvasMenuOptions = function () {
                    const n = a.apply(this, arguments);
                    return (n.push(...getMenuItems()), n);
                };
            }
            return n.apply(this, arguments);
        };
    },
});
