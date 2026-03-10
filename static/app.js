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

    const apiotBadge = document.getElementById('apiot-badge');

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

    let activeDevices = {};
    let selectedRunId = null;
    let lastAgentState = {};
    let agentActive = false;

    // How many seconds an attack arrow stays visible after the last attack
    const ATTACK_EDGE_TTL = 15;

    function deriveRisk(agentInfo) {
        if (!agentInfo) return 'none';
        if (agentInfo.remediation) return 'patched';
        if ((agentInfo.vulnerabilities || []).length > 0) return 'exploited';
        if ((agentInfo.attacks || {}).attack_count > 0) return 'attacked';
        if (agentInfo.ports && Object.keys(agentInfo.ports).length > 0) return 'recon';
        return 'none';
    }

    const RISK_STYLES = {
        none:     { border: '#0f172a', borderWidth: 2, label: '', icon: '' },
        recon:    { border: '#6366f1', borderWidth: 3, label: '', icon: '' },
        attacked: { border: '#f59e0b', borderWidth: 3, label: '', icon: ' ⚡' },
        exploited:{ border: '#ef4444', borderWidth: 4, label: '', icon: ' 🔓' },
        patched:  { border: '#10b981', borderWidth: 4, label: '', icon: ' ✅' },
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

    function ensureSwitchNode(id, label, borderColor) {
        if (!nodes.get(id)) {
            nodes.add({
                id: id,
                label: label,
                shape: 'box',
                color: { background: '#1e293b', border: borderColor },
                font: { color: borderColor, size: 14, bold: true },
                margin: 10,
            });
        }
    }

    // Remove all APIOT-related visual elements
    function clearAgentVisuals() {
        if (nodes.get('apiot_agent')) nodes.remove('apiot_agent');
        edges.get().forEach(e => {
            if (e.id.startsWith('atk_') || e.id === 'e_apiot_switch' || e.id === 'e_apiot_internal') {
                edges.remove(e.id);
            }
        });
        // Reset all device node borders back to default
        Object.keys(activeDevices).forEach(runId => {
            const d = activeDevices[runId];
            let bgColor = '#3b82f6';
            if (d.arch === 'armel') bgColor = '#10b981';
            else if (d.arch === 'cortex-m3') bgColor = '#f59e0b';
            if (!d.alive) bgColor = '#ef4444';
            nodes.update({
                id: runId,
                label: `${d.firmware_id}\n${d.ip || 'Waiting DHCP...'}`,
                color: { background: bgColor, border: '#0f172a' },
                borderWidth: 2,
                title: `MAC: ${d.mac}<br>PID: ${d.pid}`,
            });
        });
    }

    function fetchTopology() {
        Promise.all([
            fetch('/api/topology').then(r => r.json()),
            fetch('/api/traffic_stats').then(r => r.json()),
            fetch('/api/agent_state').then(r => r.json()).catch(() => ({ active: false, hosts: {} }))
        ])
        .then(([devices, trafficStats, agentState]) => {
            const nowActive = !!(agentState && agentState.active);
            const agentHosts = (agentState && agentState.hosts) || {};
            const serverTime = agentState.server_time || (Date.now() / 1000);

            // Agent just went away — clean up everything
            if (agentActive && !nowActive) {
                clearAgentVisuals();
            }
            agentActive = nowActive;
            lastAgentState = agentHosts;

            const newIds = new Set(devices.map(d => d.id));
            const currentIds = new Set(Object.keys(activeDevices));
            const isMesh = Object.keys(trafficStats).length > 0;

            nodeCountLabel.textContent = `${devices.length} nodes`;

            // APIOT badge visibility
            if (apiotBadge) {
                apiotBadge.style.display = nowActive ? 'flex' : 'none';
            }

            // Track which bridges are actually in use
            const usedBridges = new Set();
            devices.forEach(d => {
                usedBridges.add(d.bridge || 'br0');
                if (d.ip_internal !== undefined) usedBridges.add('br_internal');
            });

            if (usedBridges.has('br0')) {
                ensureSwitchNode('switch', 'br0\n192.168.100.1', '#3b82f6');
            } else if (nodes.get('switch')) {
                nodes.remove('switch');
            }

            if (usedBridges.has('br_internal')) {
                ensureSwitchNode('internal_switch', 'br_internal\n192.168.200.1', '#eab308');
            } else if (nodes.get('internal_switch')) {
                nodes.remove('internal_switch');
            }

            // APIOT agent node — only when active
            if (nowActive) {
                // Determine which bridges APIOT is interacting with
                const agentIps = Object.keys(agentHosts);
                const touchesBr0 = agentIps.some(ip => ip.startsWith('192.168.100.'));
                const touchesInternal = agentIps.some(ip => ip.startsWith('192.168.200.'));

                if (!nodes.get('apiot_agent')) {
                    nodes.add({
                        id: 'apiot_agent',
                        label: 'APIOT\nAgent',
                        shape: 'diamond',
                        size: 20,
                        color: { background: '#7c3aed', border: '#c4b5fd' },
                        font: { color: '#c4b5fd', size: 12, bold: true },
                        borderWidth: 3,
                    });
                }

                if (touchesBr0 && nodes.get('switch') && !edges.get('e_apiot_switch')) {
                    edges.add({
                        id: 'e_apiot_switch',
                        from: 'apiot_agent',
                        to: 'switch',
                        length: 250,
                        color: { color: 'rgba(124,58,237,0.4)' },
                        dashes: [5, 5],
                        width: 1,
                    });
                } else if (!touchesBr0 && edges.get('e_apiot_switch')) {
                    edges.remove('e_apiot_switch');
                }

                if (touchesInternal && nodes.get('internal_switch') && !edges.get('e_apiot_internal')) {
                    edges.add({
                        id: 'e_apiot_internal',
                        from: 'apiot_agent',
                        to: 'internal_switch',
                        length: 250,
                        color: { color: 'rgba(124,58,237,0.4)' },
                        dashes: [5, 5],
                        width: 1,
                    });
                } else if (!touchesInternal && edges.get('e_apiot_internal')) {
                    edges.remove('e_apiot_internal');
                }
            } else if (nodes.get('apiot_agent')) {
                clearAgentVisuals();
            }

            // Track which attack edges should be alive this cycle
            const liveAtkEdges = new Set();

            devices.forEach(d => {
                const runId = d.id;
                const isNew = !activeDevices[runId];
                activeDevices[runId] = d;

                const isAlive = d.alive;
                const agentInfo = nowActive ? (agentHosts[d.ip] || null) : null;
                const risk = deriveRisk(agentInfo);
                const riskStyle = nowActive ? (RISK_STYLES[risk] || RISK_STYLES.none) : RISK_STYLES.none;

                let bgColor = '#3b82f6';
                if (d.arch === 'armel') bgColor = '#10b981';
                else if (d.arch === 'cortex-m3') bgColor = '#f59e0b';
                if (!isAlive) bgColor = '#ef4444';

                const label = `${d.firmware_id}\n${d.ip || 'Waiting DHCP...'}${riskStyle.icon}`;

                let tooltip = `MAC: ${d.mac}<br>PID: ${d.pid}`;
                if (agentInfo) {
                    const ports = Object.keys(agentInfo.ports || {});
                    if (ports.length) tooltip += `<br>Ports: ${ports.join(', ')}`;
                    tooltip += `<br>Risk: ${risk}`;
                    const atkCount = (agentInfo.attacks || {}).attack_count || 0;
                    if (atkCount) tooltip += `<br>Attacks: ${atkCount}`;
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

                    const parentSwitch = (d.bridge === 'br_internal') ? 'internal_switch' : 'switch';
                    edges.add({ id: `e_${runId}`, from: parentSwitch, to: runId, length: 200, dashes: !isAlive, hidden: isMesh });

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

                // Transient attack edges — only show if last attack was within TTL
                if (nowActive && agentInfo) {
                    const lastTime = (agentInfo.attacks || {}).last_attack_time || 0;
                    const age = serverTime - lastTime;
                    const atkEdgeId = `atk_${runId}`;

                    if (age <= ATTACK_EDGE_TTL && lastTime > 0) {
                        liveAtkEdges.add(atkEdgeId);
                        const opacity = Math.max(0.15, 1 - (age / ATTACK_EDGE_TTL));
                        const atkColor = risk === 'exploited' ? `rgba(239,68,68,${opacity})`
                                       : risk === 'patched'  ? `rgba(16,185,129,${opacity})`
                                       : `rgba(245,158,11,${opacity})`;
                        if (!edges.get(atkEdgeId)) {
                            edges.add({
                                id: atkEdgeId,
                                from: 'apiot_agent',
                                to: runId,
                                length: 300,
                                color: { color: atkColor },
                                width: 2,
                                dashes: [8, 4],
                                arrows: { to: { enabled: true, scaleFactor: 0.5 } },
                            });
                        } else {
                            edges.update({
                                id: atkEdgeId,
                                color: { color: atkColor },
                            });
                        }
                    }
                }

                if (selectedRunId === runId) {
                    overlayIp.textContent = d.ip || 'N/A';
                    overlayStatus.innerHTML = isAlive
                        ? '<span style="color:var(--success)">Running</span>'
                        : '<span style="color:var(--danger)">Terminated/Down</span>';
                    if (!isAlive) killBtn.style.display = 'none';
                    populateAgentOverlay(agentInfo);
                }
            });

            // Remove stale attack edges (expired TTL)
            edges.get().forEach(e => {
                if (e.id.startsWith('atk_') && !liveAtkEdges.has(e.id)) {
                    edges.remove(e.id);
                }
            });

            // Remove stale device nodes
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
        const risk = deriveRisk(agentInfo);
        if (!agentActive || !agentInfo || risk === 'none') {
            agentSection.style.display = 'none';
            return;
        }
        agentSection.style.display = 'block';
        detailRisk.innerHTML = riskHtml(risk);

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

    // --- 3. Readiness Indicator ---
    const statusText = document.getElementById('status-text');
    const statusIndicator = document.getElementById('system-status');
    const pulseEl = statusIndicator.querySelector('.pulse');

    function fetchReady() {
        fetch('/api/ready').then(r => r.json()).then(data => {
            if (data.ready) {
                statusText.textContent = `Ready (${data.total} devices)`;
                statusIndicator.style.color = 'var(--success)';
                pulseEl.classList.remove('pulse-warn');
                pulseEl.classList.add('pulse-ready');
            } else {
                const done = data.total - data.pending;
                statusText.textContent = `Initializing... (${done}/${data.total} IPs assigned)`;
                statusIndicator.style.color = 'var(--warning)';
                pulseEl.classList.remove('pulse-ready');
                pulseEl.classList.add('pulse-warn');
            }
        }).catch(() => {});
    }

    fetchTopology();
    fetchReady();
    setInterval(fetchTopology, 2000);
    setInterval(fetchReady, 3000);

});
