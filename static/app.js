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

    const agentSection = document.getElementById('agent-section');
    const detailRisk = document.getElementById('detail-risk');
    const detailPorts = document.getElementById('detail-ports');
    const detailAttacks = document.getElementById('detail-attacks');
    const detailLastTool = document.getElementById('detail-last-tool');
    const detailRemediation = document.getElementById('detail-remediation');

    let logsReceived = 0;
    let autoScroll = true;

    // --- 1. Log Streaming (SSE) ---
    const evtSource = new EventSource('/api/logs/stream');

    evtSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        const msg = data.message;

        const line = document.createElement('div');
        line.className = 'log-line';

        if (msg.includes('[INFO]')) line.classList.add('log-INFO');
        else if (msg.includes('[ERROR]')) line.classList.add('log-ERROR');
        else if (msg.includes('[WARNING]')) line.classList.add('log-WARNING');

        if (msg.includes('APIOT')) line.classList.add('log-APIOT');

        line.textContent = msg;
        logOutput.appendChild(line);
        logsReceived++;

        logCountTrigger.textContent = `${logsReceived} messages`;

        if (logOutput.children.length > 500) {
            logOutput.removeChild(logOutput.firstChild);
        }

        if (autoScroll) {
            logOutput.scrollTop = logOutput.scrollHeight;
        }
    };

    logOutput.addEventListener('scroll', () => {
        const isAtBottom = logOutput.scrollHeight - logOutput.clientHeight <= logOutput.scrollTop + 50;
        autoScroll = isAtBottom;
    });

    // --- 2. Topology Visualization (Vis.js) ---
    const container = document.getElementById('network');
    const nodes = new vis.DataSet([]);
    const edges = new vis.DataSet([]);
    const visData = { nodes: nodes, edges: edges };

    const options = {
        nodes: {
            shape: 'dot',
            size: 24,
            font: { color: '#f8fafc', face: 'Inter', size: 12 },
            borderWidth: 2,
            shadow: { enabled: true, color: 'rgba(0,0,0,0.5)', size: 10, x: 0, y: 5 }
        },
        edges: {
            width: 2,
            color: { color: '#475569', highlight: '#3b82f6' },
            smooth: { type: 'continuous' }
        },
        physics: {
            barnesHut: { gravitationalConstant: -3000, springConstant: 0.04, springLength: 150 },
            stabilization: { iterations: 150 }
        },
        interaction: { hover: true, tooltipDelay: 200 }
    };

    const network = new vis.Network(container, visData, options);

    nodes.add({
        id: 'switch',
        label: 'br0\n192.168.100.1',
        shape: 'box',
        color: { background: '#1e293b', border: '#3b82f6' },
        font: { color: '#3b82f6', size: 14, bold: true },
        margin: 10,
        fixed: { x: false, y: false }
    });

    let activeDevices = {};
    let selectedRunId = null;
    let lastAgentState = {};

    // Risk level visual config
    const RISK_STYLES = {
        none:     { border: '#0f172a', borderWidth: 2, label: '' },
        recon:    { border: '#6366f1', borderWidth: 3, label: '' },
        attacked: { border: '#f59e0b', borderWidth: 3, label: ' [ATK]' },
        exploited:{ border: '#ef4444', borderWidth: 4, label: ' [VULN]' },
        patched:  { border: '#10b981', borderWidth: 4, label: ' [FIX]' },
    };

    function riskHtml(level) {
        const map = {
            none: '<span style="color:var(--text-muted)">None</span>',
            recon: '<span style="color:#6366f1">Recon</span>',
            attacked: '<span style="color:var(--warning)">Under Attack</span>',
            exploited: '<span style="color:var(--danger)">Exploited</span>',
            patched: '<span style="color:var(--success)">Patched</span>',
        };
        return map[level] || map.none;
    }

    function fetchTopology() {
        Promise.all([
            fetch('/api/topology').then(r => r.json()),
            fetch('/api/traffic_stats').then(r => r.json()),
            fetch('/api/agent_state').then(r => r.json()).catch(() => ({ hosts: {} }))
        ])
        .then(([devices, trafficStats, agentState]) => {
            const agentHosts = (agentState && agentState.hosts) || {};
            lastAgentState = agentHosts;

            const newIds = new Set(devices.map(d => d.id));
            const currentIds = new Set(Object.keys(activeDevices));
            const isMesh = Object.keys(trafficStats).length > 0;

            nodeCountLabel.textContent = `${devices.length} nodes`;

            // Determine if any agent activity exists to show the APIOT node
            const hasAgentActivity = Object.keys(agentHosts).length > 0;

            if (hasAgentActivity && !nodes.get('apiot_agent')) {
                nodes.add({
                    id: 'apiot_agent',
                    label: 'APIOT\nAgent',
                    shape: 'diamond',
                    size: 20,
                    color: { background: '#7c3aed', border: '#c4b5fd' },
                    font: { color: '#c4b5fd', size: 12, bold: true },
                    borderWidth: 3,
                });
                edges.add({
                    id: 'e_apiot_switch',
                    from: 'apiot_agent',
                    to: 'switch',
                    length: 250,
                    color: { color: 'rgba(124,58,237,0.4)' },
                    dashes: [5, 5],
                    width: 1,
                });
            } else if (!hasAgentActivity && nodes.get('apiot_agent')) {
                nodes.remove('apiot_agent');
                edges.remove('e_apiot_switch');
            }

            devices.forEach(d => {
                const runId = d.id;
                const isNew = !activeDevices[runId];
                activeDevices[runId] = d;

                const isAlive = d.alive;
                const agentInfo = agentHosts[d.ip] || null;
                const risk = agentInfo ? agentInfo.risk_level || 'none' : 'none';
                const riskStyle = RISK_STYLES[risk] || RISK_STYLES.none;

                let bgColor = '#3b82f6';
                if (d.arch === 'armel') bgColor = '#10b981';
                else if (d.arch === 'cortex-m3') bgColor = '#f59e0b';
                if (!isAlive) bgColor = '#ef4444';

                const label = `${d.firmware_id}\n${d.ip || 'Waiting DHCP...'}${riskStyle.label}`;

                let tooltip = `MAC: ${d.mac}<br>PID: ${d.pid}`;
                if (agentInfo) {
                    const ports = Object.keys(agentInfo.ports || {});
                    tooltip += `<br>Ports: ${ports.join(', ') || 'n/a'}`;
                    tooltip += `<br>Risk: ${risk}`;
                    tooltip += `<br>Attacks: ${(agentInfo.attacks || {}).attack_count || 0}`;
                    if (agentInfo.remediation) tooltip += '<br>Remediation: applied';
                }

                if (isNew) {
                    nodes.add({
                        id: runId,
                        label: label,
                        color: { background: bgColor, border: riskStyle.border },
                        borderWidth: riskStyle.borderWidth,
                        title: tooltip,
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
                        edges.add({ id: `e_${runId}`, from: 'internal_switch', to: runId, length: 200, dashes: !isAlive, hidden: isMesh });
                    } else {
                        edges.add({ id: `e_${runId}`, from: 'switch', to: runId, length: 200, dashes: !isAlive, hidden: isMesh });
                    }

                    if (d.ip_internal !== undefined) {
                        edges.add({ id: `e_int_${runId}`, from: 'internal_switch', to: runId, length: 200, dashes: !isAlive, hidden: isMesh });
                    }
                } else {
                    nodes.update({
                        id: runId,
                        label: label,
                        color: { background: bgColor, border: riskStyle.border },
                        borderWidth: riskStyle.borderWidth,
                        title: tooltip,
                    });
                    edges.update({ id: `e_${runId}`, dashes: !isAlive, hidden: isMesh });
                    if (d.ip_internal !== undefined && edges.get(`e_int_${runId}`)) {
                        edges.update({ id: `e_int_${runId}`, dashes: !isAlive, hidden: isMesh });
                    }
                }

                // APIOT attack edges
                if (hasAgentActivity && agentInfo && (agentInfo.attacks || {}).attack_count > 0) {
                    const atkEdgeId = `atk_${runId}`;
                    const atkCount = agentInfo.attacks.attack_count;
                    const atkColor = risk === 'exploited' ? 'rgba(239,68,68,0.6)'
                                   : risk === 'patched'  ? 'rgba(16,185,129,0.5)'
                                   : 'rgba(245,158,11,0.5)';
                    if (!edges.get(atkEdgeId)) {
                        edges.add({
                            id: atkEdgeId,
                            from: 'apiot_agent',
                            to: runId,
                            length: 300,
                            color: { color: atkColor },
                            width: Math.min(1 + atkCount, 5),
                            dashes: [8, 4],
                            arrows: { to: { enabled: true, scaleFactor: 0.5 } },
                        });
                    } else {
                        edges.update({
                            id: atkEdgeId,
                            color: { color: atkColor },
                            width: Math.min(1 + atkCount, 5),
                        });
                    }
                }

                // Update overlay if this node is selected
                if (selectedRunId === runId) {
                    overlayIp.textContent = d.ip || 'N/A';
                    overlayStatus.innerHTML = isAlive
                        ? '<span style="color:var(--success)">Running</span>'
                        : '<span style="color:var(--danger)">Terminated/Down</span>';
                    if (!isAlive) killBtn.style.display = 'none';
                    populateAgentOverlay(agentInfo);
                }
            });

            // Remove stale nodes
            for (let cid of currentIds) {
                if (!newIds.has(cid)) {
                    nodes.remove(cid);
                    edges.remove(`e_${cid}`);
                    edges.remove(`e_int_${cid}`);
                    edges.remove(`atk_${cid}`);
                    delete activeDevices[cid];
                    if (selectedRunId === cid) closeOverlay();
                }
            }

            // Mesh traffic edges
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

    function populateAgentOverlay(agentInfo) {
        if (!agentInfo || agentInfo.risk_level === 'none') {
            agentSection.style.display = 'none';
            return;
        }
        agentSection.style.display = 'block';
        detailRisk.innerHTML = riskHtml(agentInfo.risk_level);

        const ports = Object.entries(agentInfo.ports || {});
        detailPorts.textContent = ports.length
            ? ports.map(([p, info]) => `${p}/${info.protocol || '?'} (${info.service || '?'})`).join(', ')
            : 'n/a';

        const atk = agentInfo.attacks || {};
        detailAttacks.textContent = atk.attack_count
            ? `${atk.attack_count} total — last: ${atk.last_outcome || '?'}`
            : 'None';

        detailLastTool.textContent = atk.last_attack_tool || 'n/a';

        const rem = agentInfo.remediation;
        if (rem) {
            detailRemediation.innerHTML = `<span style="color:var(--success)">${rem.last_rule || 'applied'}</span>`;
        } else {
            detailRemediation.textContent = 'None';
        }
    }

    // --- Interactions ---
    network.on('click', (params) => {
        if (params.nodes.length > 0) {
            const nodeId = params.nodes[0];
            if (nodeId === 'switch' || nodeId === 'internal_switch' || nodeId === 'apiot_agent') return;

            const dev = activeDevices[nodeId];
            if (dev) {
                selectedRunId = nodeId;
                overlayTitle.textContent = nodeId.substring(0, 20) + '...';
                overlayIp.textContent = dev.ip || 'Waiting for DHCP...';
                overlayFw.textContent = `${dev.firmware_id} (${dev.arch})`;
                overlayStatus.innerHTML = dev.alive
                    ? '<span style="color:var(--success)">Running</span>'
                    : '<span style="color:var(--danger)">Terminated/Down</span>';

                killBtn.style.display = dev.alive ? 'block' : 'none';
                overlay.classList.add('visible');

                const agentInfo = lastAgentState[dev.ip] || null;
                populateAgentOverlay(agentInfo);
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
            .then(() => {
                killBtn.textContent = "Kill Device";
                fetchTopology();
            });
    });

    fetchTopology();
    setInterval(fetchTopology, 2000);

});
