/**
 * MiniMax H3 frontend compatibility hooks.
 * SPDX-License-Identifier: Apache-2.0
 */

import { app } from './comfy/index.js';
import { migrateMiniMaxH3Workflow } from './eclipse-minimax-h3-workflow-migration.js';

app.registerExtension({
    name: 'Eclipse.MiniMaxH3WorkflowMigration',
    beforeConfigureGraph(graphData) {
        migrateMiniMaxH3Workflow(graphData);
    },
});
