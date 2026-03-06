// static/app.js

document.addEventListener('DOMContentLoaded', () => {

    const logOutput = document.getElementById('log-output');
    const logCountTrigger = document.getElementById('log-count');
    const nodeCountLabel = document.getElementById('node-count');

    const overlay = document.getElementById('node-details');
    const overlayTitle = document.getElementById('detail-title');
    const overlayIp = document.getElementById('detail-ip');
    const overlayFw = document.getElementById('detail-firmware');
    const overlayStatus = document.getElementById('detail-status');
    const killBtn = document.getElementById('kill-btn');
    const closeOverlayBtn = document.getElementById('close-overlay');

    let logsReceived = 0;
    let autoScroll = true;

    // --- 1. Log Streaming (SSE) ---
    const evtSource = new EventSource('/api/logs/stream');

    evtSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        const msg = data.message;

        const line = document.createElement('div');
        line.className = 'log-line';

        // Basic coloring based on content
        if (msg.includes('[INFO]')) line.classList.add('log-INFO');
        else if (msg.includes('[ERROR]')) line.classList.add('log-ERROR');
        else if (msg.includes('[WARNING]')) line.classList.add('log-WARNING');

        line.textContent = msg;
        logOutput.appendChild(line);
        logsReceived++;

        logCountTrigger.textContent = `${logsReceived} messages`;

        // Remove old logs to prevent DOM bloat
        if (logOutput.children.length > 500) {
            logOutput.removeChild(logOutput.firstChild);
        }

        if (autoScroll) {
            logOutput.scrollTop = logOutput.scrollHeight;
        }
    };

    // Pause auto-scroll if user scrolls up
    logOutput.addEventListener('scroll', () => {
        const isAtBottom = logOutput.scrollHeight - logOutput.clientHeight <= logOutput.scrollTop + 50;
        autoScroll = isAtBottom;
    });


    // --- 2. Topology Visualization (Vis.js) ---
    // Create an empty network
    const container = document.getElementById('network');
    const nodes = new vis.DataSet([]);
    const edges = new vis.DataSet([]);
    const data = { nodes: nodes, edges: edges };

    // Vis.js visual styling options
    const options = {
        nodes: {
            shape: 'dot',
            size: 24,
            font: {
                color: '#f8fafc',
                face: 'Inter',
                size: 12
            },
            borderWidth: 2,
            shadow: {
                enabled: true,
                color: 'rgba(0,0,0,0.5)',
                size: 10,
                x: 0,
                y: 5
            }
        },
        edges: {
            width: 2,
            color: { color: '#475569', highlight: '#3b82f6' },
            smooth: { type: 'continuous' }
        },
        physics: {
            barnesHut: {
                gravitationalConstant: -3000,
                springConstant: 0.04,
                springLength: 150
            },
            stabilization: { iterations: 150 }
        },
        interaction: {
            hover: true,
            tooltipDelay: 200
        }
    };

    const network = new vis.Network(container, data, options);

    // Switch (Root node)
    nodes.add({
        id: 'switch',
        label: 'br0\n192.168.100.1',
        shape: 'box',
        color: { background: '#1e293b', border: '#3b82f6' },
        font: { color: '#3b82f6', size: 14, bold: true },
        margin: 10,
        fixed: { x: false, y: false }
    });

    let activeDevices = {}; // map of run_id -> device info
    let selectedRunId = null;

    function fetchTopology() {
        Promise.all([
            fetch('/api/topology').then(res => res.json()),
            fetch('/api/traffic_stats').then(res => res.json())
        ])
            .then(([devices, trafficStats]) => {
                // Remove nodes that no longer exist OR mark them as dead
                const newIds = new Set(devices.map(d => d.id));
                const currentIds = new Set(Object.keys(activeDevices));

                nodeCountLabel.textContent = `${devices.length} nodes`;

                const isMesh = Object.keys(trafficStats).length > 0;

                // Update or Add
                devices.forEach(d => {
                    const runId = d.id;
                    const isNew = !activeDevices[runId];
                    activeDevices[runId] = d;

                    const isAlive = d.alive;
                    const label = `${d.firmware_id}\n${d.ip || 'Waiting DHCP...'}`;

                    let bgColor = '#3b82f6'; // blue (MIPS)
                    if (d.arch === 'armel') bgColor = '#10b981'; // green
                    else if (d.arch === 'cortex-m3') bgColor = '#f59e0b'; // amber

                    if (!isAlive) {
                        bgColor = '#ef4444'; // red (dead)
                    }

                    if (isNew) {
                        nodes.add({
                            id: runId,
                            label: label,
                            color: { background: bgColor, border: '#0f172a' },
                            title: `MAC: ${d.mac}<br>PID: ${d.pid}` // tooltip
                        });

                        if (d.bridge === 'br_internal' || d.ip_internal !== undefined) {
                            if (!nodes.get('internal_switch')) {
                                nodes.add({
                                    id: 'internal_switch',
                                    label: 'br_internal\n192.168.200.1',
                                    shape: 'box',
                                    color: { background: '#1e293b', border: '#eab308' },
                                    font: { color: '#eab308', size: 14, bold: true },
                                    margin: 10
                                });
                            }
                        }

                        if (d.bridge === 'br_internal') {
                            edges.add({
                                id: `e_${runId}`,
                                from: 'internal_switch',
                                to: runId,
                                length: 200,
                                dashes: !isAlive,
                                hidden: isMesh
                            });
                        } else {
                            edges.add({
                                id: `e_${runId}`,
                                from: 'switch',
                                to: runId,
                                length: 200,
                                dashes: !isAlive,
                                hidden: isMesh
                            });
                        }

                        if (d.ip_internal !== undefined) {
                            edges.add({
                                id: `e_int_${runId}`,
                                from: 'internal_switch',
                                to: runId,
                                length: 200,
                                dashes: !isAlive,
                                hidden: isMesh
                            });
                        }
                    } else {
                        nodes.update({
                            id: runId,
                            label: label,
                            color: { background: bgColor }
                        });
                        edges.update({
                            id: `e_${runId}`,
                            dashes: !isAlive,
                            hidden: isMesh
                        });
                        if (d.ip_internal !== undefined && edges.get(`e_int_${runId}`)) {
                            edges.update({
                                id: `e_int_${runId}`,
                                dashes: !isAlive,
                                hidden: isMesh
                            });
                        }
                    }

                    // If this is the currently selected node, update overlay
                    if (selectedRunId === runId) {
                        overlayIp.textContent = d.ip || 'N/A';
                        overlayStatus.innerHTML = isAlive
                            ? '<span style="color:var(--success)">Running</span>'
                            : '<span style="color:var(--danger)">Terminated/Down</span>';
                        if (!isAlive) killBtn.style.display = 'none';
                    }
                });

                // Check for removals (not strictly necessary since we just mark them dead, 
                // but good to cleanup if the backend fully drops them)
                for (let cid of currentIds) {
                    if (!newIds.has(cid)) {
                        nodes.remove(cid);
                        edges.remove(`e_${cid}`);
                        edges.remove(`e_int_${cid}`);
                        delete activeDevices[cid];
                        if (selectedRunId === cid) closeOverlay();
                    }
                }

                if (isMesh) {
                    const ipToId = {};
                    devices.forEach(d => { if (d.ip) ipToId[d.ip] = d.id; });

                    Object.keys(trafficStats).forEach(srcIp => {
                        const srcId = ipToId[srcIp];
                        if (!srcId) return;

                        trafficStats[srcIp].connections.forEach(dstIp => {
                            const dstId = ipToId[dstIp];
                            if (!dstId) return;

                            const edgeId = `mesh_${srcId}_${dstId}`;
                            if (!edges.get(edgeId)) {
                                edges.add({
                                    id: edgeId,
                                    from: srcId,
                                    to: dstId,
                                    color: { color: 'rgba(59, 130, 246, 0.4)' },
                                    width: 2
                                });
                            }
                        });
                    });
                }
            })
            .catch(err => console.error("Topology fetch error", err));
    }

    // Interactions
    network.on('click', (params) => {
        if (params.nodes.length > 0) {
            const nodeId = params.nodes[0];
            if (nodeId === 'switch') return; // ignore clicking the switch

            const dev = activeDevices[nodeId];
            if (dev) {
                selectedRunId = nodeId;
                overlayTitle.textContent = nodeId.substring(0, 8) + '...';
                overlayIp.textContent = dev.ip || 'Waiting for DHCP...';
                overlayFw.textContent = `${dev.firmware_id} (${dev.arch})`;
                overlayStatus.innerHTML = dev.alive
                    ? '<span style="color:var(--success)">Running</span>'
                    : '<span style="color:var(--danger)">Terminated/Down</span>';

                killBtn.style.display = dev.alive ? 'block' : 'none';
                overlay.classList.add('visible');
            }
        } else {
            closeOverlay();
        }
    });

    const closeOverlay = () => {
        overlay.classList.remove('visible');
        selectedRunId = null;
        network.unselectAll();
    };

    closeOverlayBtn.addEventListener('click', closeOverlay);

    killBtn.addEventListener('click', () => {
        if (!selectedRunId) return;
        killBtn.textContent = "Killing...";
        fetch(`/api/kill/${selectedRunId}`, { method: 'POST' })
            .then(res => res.json())
            .then(btn => {
                killBtn.textContent = "Kill Device";
                fetchTopology(); // manual refresh immediately
            });
    });

    // Start Polling 
    fetchTopology();
    setInterval(fetchTopology, 2000);

});
