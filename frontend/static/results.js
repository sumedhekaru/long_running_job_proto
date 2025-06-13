// results.js: Handles item dropdown population on the Results page

document.addEventListener('DOMContentLoaded', function() {
    const fetchBtn = document.getElementById('fetch-items');
    const jobInput = document.getElementById('job-id');
    const itemSelect = document.getElementById('item-select');
    const plotDiv = document.getElementById('plotly-chart');

    fetchBtn.addEventListener('click', async function() {
        const jobId = jobInput.value.trim();
        if (!jobId) {
            alert('Please enter a Job ID.');
            return;
        }
        fetchBtn.disabled = true;
        itemSelect.disabled = true;
        itemSelect.innerHTML = '<option>Loading...</option>';
        try {
            const resp = await fetch(`/api/items/${jobId}`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            if (!data.items || data.items.length === 0) {
                itemSelect.innerHTML = '<option>No items found</option>';
                itemSelect.disabled = true;
            } else {
                itemSelect.innerHTML = '<option value="">Select an item</option>' +
                    data.items.map(item => `<option value="${item}">${item}</option>`).join('');
                itemSelect.disabled = false;
            }
        } catch (err) {
            itemSelect.innerHTML = '<option>Error loading items</option>';
            itemSelect.disabled = true;
            alert('Failed to load items: ' + err.message);
        } finally {
            fetchBtn.disabled = false;
        }
    });

    itemSelect.addEventListener('change', async function() {
        const jobId = jobInput.value.trim();
        const itemNbr = itemSelect.value;
        if (!jobId || !itemNbr) {
            Plotly.purge(plotDiv);
            return;
        }
        plotDiv.innerHTML = 'Loading...';
        try {
            const resp = await fetch(`/api/itemdata/${jobId}/${itemNbr}`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            // Prepare traces
            const histTrace = {
                x: data.historical.map(d => d.ds),
                y: data.historical.map(d => d.y),
                mode: 'lines+markers',
                name: 'Historical',
                line: {color: '#1f77b4'}
            };
            const fcstTrace = {
                x: data.forecast.map(d => d.ds),
                y: data.forecast.map(d => d.yhat),
                mode: 'lines+markers',
                name: 'Forecast',
                line: {color: '#ff7f0e', dash: 'dash'}
            };
            Plotly.newPlot(plotDiv, [histTrace, fcstTrace], {
                title: `Item ${itemNbr}: Historical & Forecast`,
                xaxis: {title: 'Week Ending'},
                yaxis: {title: 'Sales'},
                legend: {orientation: 'h', x: 0.2, y: 1.15},
                margin: {t: 50}
            }, {responsive: true});
        } catch (err) {
            plotDiv.innerHTML = '<div class="text-danger">Failed to load plot: ' + err.message + '</div>';
        }
    });
});
