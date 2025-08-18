"""
JavaScript functionality for the documentation system.
"""


def get_documentation_javascript(methods_json: str) -> str:
    """Get the interactive JavaScript for the documentation"""
    return f"""
        const methods = {methods_json};

        // Dynamically determine the base URL for deployed environments
        function getBaseUrl() {{
            const protocol = window.location.protocol;
            const hostname = window.location.hostname;
            const port = window.location.port;

            let baseUrl;

            // For local development (localhost or 127.0.0.1), use port 4000
            if (hostname === 'localhost' || hostname === '127.0.0.1') {{
                baseUrl = `${{protocol}}//${{hostname}}:4000/api`;
            }}
            // For deployed environments, use the same host and port as the current page
            else if (port && port !== '80' && port !== '443') {{
                baseUrl = `${{protocol}}//${{hostname}}:${{port}}/api`;
            }}
            // For standard HTTP/HTTPS ports, omit the port
            else {{
                baseUrl = `${{protocol}}//${{hostname}}/api`;
            }}

            console.log('API Base URL:', baseUrl);
            return baseUrl;
        }}

        const baseUrl = getBaseUrl();

        // Update the base URL display in the header
        document.getElementById('baseUrlDisplay').textContent = baseUrl;

        // Utility function for formatting category names
        function formatCategoryClass(category) {{
            return category.toLowerCase().replace(/\\s+/g, '').replace(/[^a-z]/g, '');
        }}

        // Group methods by category
        const categories = {{}};
        methods.forEach(method => {{
            if (!categories[method.category]) {{
                categories[method.category] = [];
            }}
            categories[method.category].push(method);
        }});

        // Build sidebar
        const sidebarContent = document.getElementById('sidebarContent');
        Object.keys(categories).sort().forEach(category => {{
            const section = document.createElement('div');
            section.className = 'category-section';

            const header = document.createElement('div');
            header.className = 'category-header';
            header.innerHTML = `
                <span>${{category}}</span>
                <span class="category-toggle">▼</span>
            `;
            section.appendChild(header);

            const methodList = document.createElement('div');
            methodList.className = 'method-list active';

            categories[category].sort((a, b) => a.name.localeCompare(b.name)).forEach(method => {{
                const link = document.createElement('a');
                link.href = `#${{method.name}}`;
                link.className = 'method-link';
                link.textContent = method.name;
                link.onclick = (e) => {{
                    e.preventDefault();
                    document.querySelectorAll('.method-link').forEach(l => l.classList.remove('active'));
                    link.classList.add('active');
                    const target = document.getElementById(method.name);
                    if (target) {{
                        target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
                        // Expand the method if it's not already expanded
                        if (!target.classList.contains('expanded')) {{
                            target.querySelector('.method-header').click();
                        }}
                    }}
                }};
                methodList.appendChild(link);
            }});

            section.appendChild(methodList);
            sidebarContent.appendChild(section);

            // Toggle category
            header.onclick = () => {{
                methodList.classList.toggle('active');
                const toggle = header.querySelector('.category-toggle');
                toggle.textContent = methodList.classList.contains('active') ? '▼' : '▶';
            }};
        }});

        // Build methods
        const methodsContainer = document.getElementById('methodsContainer');
        Object.keys(categories).sort().forEach(category => {{
            const group = document.createElement('div');
            group.className = 'method-group';

            const groupHeader = document.createElement('div');
            groupHeader.className = 'method-group-header';
            groupHeader.textContent = category;
            group.appendChild(groupHeader);

            categories[category].sort((a, b) => a.name.localeCompare(b.name)).forEach(method => {{
                const methodDiv = document.createElement('div');
                methodDiv.className = 'method';
                methodDiv.id = method.name;

                const categoryClass = formatCategoryClass(category);

                // Method header
                const header = document.createElement('div');
                header.className = 'method-header';
                header.innerHTML = `
                    <span class="method-badge ${{categoryClass}}">POST</span>
                    <span class="method-name">${{method.name}}</span>
                    <span class="method-toggle">▶</span>
                `;
                methodDiv.appendChild(header);

                // Method content
                const content = document.createElement('div');
                content.className = 'method-content';

                // Description
                if (method.description) {{
                    content.innerHTML += `<div class="method-description">${{method.description}}</div>`;
                }}

                // Parameters
                if (method.parameters && method.parameters.length > 0) {{
                    content.innerHTML += `
                        <div class="parameters-section">
                            <h3 class="section-title">Parameters</h3>
                            <table class="parameters-table">
                                <thead>
                                    <tr>
                                        <th style="width: 200px">Name</th>
                                        <th style="width: 150px">Type</th>
                                        <th style="width: 100px">Required</th>
                                        <th>Description</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    ${{method.parameters.map(param => `
                                        <tr>
                                            <td><span class="param-name">${{param.name}}</span></td>
                                            <td><span class="param-type">${{param.type}}</span></td>
                                            <td><span class="${{param.required ? 'param-required' : 'param-optional'}}">${{param.required ? 'required' : 'optional'}}</span></td>
                                            <td class="param-description">${{param.description || '-'}}</td>
                                        </tr>
                                    `).join('')}}
                                </tbody>
                            </table>
                        </div>
                    `;
                }} else {{
                    content.innerHTML += '<p style="margin: 1rem 0; color: #6b7280;">No parameters required</p>';
                }}

                // Returns
                content.innerHTML += `
                    <div class="returns-section">
                        <h3 class="section-title">Returns</h3>
                        <span class="returns-type">${{method.returns}}</span>
                    </div>
                `;

                // Try it out
                const trySection = document.createElement('div');
                trySection.className = 'try-section';
                trySection.innerHTML = `
                    <div class="try-header">
                        <h3 class="section-title" style="margin: 0">Try it out</h3>
                        <button class="try-button" onclick="toggleTryIt('${{method.name}}')">Try it out</button>
                    </div>
                    <div class="try-content" id="try-${{method.name}}">
                        <div class="code-editor">
                            <div class="code-editor-label">Request Body</div>
                            <textarea class="code-editor-input" id="request-${{method.name}}">${{JSON.stringify({{
                                jsonrpc: "2.0",
                                method: method.name,
                                params: method.examples && method.examples[0] ? method.examples[0].request.params : [],
                                id: 1
                            }}, null, 2)}}</textarea>
                        </div>
                        <button class="execute-button" onclick="executeMethod('${{method.name}}')">Execute</button>
                        <div class="response-section" id="response-${{method.name}}">
                            <div class="response-header">
                                <h4 class="section-title" style="margin: 0">Response</h4>
                                <span class="response-status" id="status-${{method.name}}"></span>
                                <span class="response-time" id="time-${{method.name}}"></span>
                            </div>
                            <div class="response-body">
                                <pre id="result-${{method.name}}"></pre>
                            </div>
                        </div>
                    </div>
                `;
                content.appendChild(trySection);

                // Example
                if (method.examples && method.examples.length > 0) {{
                    content.innerHTML += `
                        <div class="example-section">
                            <h3 class="section-title">Example Request</h3>
                            <div class="example-code">
                                <pre>${{JSON.stringify(method.examples[0].request, null, 2)}}</pre>
                            </div>
                        </div>
                    `;
                }}

                methodDiv.appendChild(content);
                group.appendChild(methodDiv);

                // Toggle method
                header.onclick = () => {{
                    methodDiv.classList.toggle('expanded');
                }};
            }});

            methodsContainer.appendChild(group);
        }});

        // Search functionality
        document.getElementById('searchBox').addEventListener('input', (e) => {{
            const searchTerm = e.target.value.toLowerCase();
            document.querySelectorAll('.method').forEach(method => {{
                const name = method.id.toLowerCase();
                const description = method.querySelector('.method-description')?.textContent.toLowerCase() || '';
                const visible = name.includes(searchTerm) || description.includes(searchTerm);
                method.style.display = visible ? 'block' : 'none';
            }});

            // Hide empty groups
            document.querySelectorAll('.method-group').forEach(group => {{
                const hasVisibleMethods = Array.from(group.querySelectorAll('.method')).some(m => m.style.display !== 'none');
                group.style.display = hasVisibleMethods ? 'block' : 'none';
            }});
        }});

        // Try it out functionality
        function toggleTryIt(methodName) {{
            const tryContent = document.getElementById(`try-${{methodName}}`);
            const button = tryContent.previousElementSibling.querySelector('.try-button');

            if (tryContent.classList.contains('active')) {{
                tryContent.classList.remove('active');
                button.textContent = 'Try it out';
                button.classList.remove('cancel');
            }} else {{
                tryContent.classList.add('active');
                button.textContent = 'Cancel';
                button.classList.add('cancel');
            }}
        }}

        async function executeMethod(methodName) {{
            const requestInput = document.getElementById(`request-${{methodName}}`);
            const responseSection = document.getElementById(`response-${{methodName}}`);
            const statusSpan = document.getElementById(`status-${{methodName}}`);
            const timeSpan = document.getElementById(`time-${{methodName}}`);
            const resultPre = document.getElementById(`result-${{methodName}}`);

            try {{
                const request = JSON.parse(requestInput.value);

                // Show loading
                responseSection.classList.add('active');
                statusSpan.textContent = 'Loading...';
                statusSpan.className = 'response-status';
                timeSpan.innerHTML = '<span class="loading"></span>';
                resultPre.textContent = '';

                const startTime = Date.now();

                const response = await fetch(baseUrl, {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify(request)
                }});

                const endTime = Date.now();
                const duration = endTime - startTime;

                const result = await response.json();

                // Update status
                if (result.error) {{
                    statusSpan.textContent = `Error ${{result.error.code}}`;
                    statusSpan.className = 'response-status error';
                }} else {{
                    statusSpan.textContent = '200 OK';
                    statusSpan.className = 'response-status success';
                }}

                timeSpan.textContent = `${{duration}}ms`;
                resultPre.textContent = JSON.stringify(result, null, 2);

            }} catch (error) {{
                responseSection.classList.add('active');
                statusSpan.textContent = 'Network Error';
                statusSpan.className = 'response-status error';
                timeSpan.textContent = '';
                resultPre.textContent = error.message;
            }}
        }}
    """
