import React, { useState, useEffect, useCallback } from 'react';
import { AgGridReact } from 'ag-grid-react';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';
import { api } from '../api';
import CustomHeader from './CustomHeader';

const Dashboard = () => {
  const [rowData, setRowData] = useState([]);
  const [colDefs, setColDefs] = useState([]);
  const [globalSearch, setGlobalSearch] = useState('');
  const userRole = localStorage.getItem('role') || 'R';

  useEffect(() => {
    const init = async () => {
      const schemaRes = await api.get('/schema');
      const columns = schemaRes.data.map(col => ({
        field: col.field,
        headerName: col.header,
        headerComponent: CustomHeader,
        headerComponentParams: { type: col.type },
        editable: col.type === 'manual' && (userRole === 'W' || userRole === 'E'),
        filter: true,
        sortable: true,
      }));
      setColDefs(columns);
      loadData();
    };
    init();
  }, []);

  const loadData = async (search = '') => {
    const res = await api.get(`/servers?global_search=${encodeURIComponent(search)}`);
    setRowData(res.data.data);
  };

  useEffect(() => {
    const delay = setTimeout(() => loadData(globalSearch), 500);
    return () => clearTimeout(delay);
  }, [globalSearch]);

  const onCellValueChanged = useCallback(async (event) => {
    const { data, colDef, newValue, oldValue } = event;
    if (colDef.headerComponentParams.type !== 'manual') return;
    try {
      await api.patch(`/servers/${data.hostname}`, { field: colDef.field, value: newValue });
    } catch (err) {
      event.node.setDataValue(colDef.field, oldValue);
      alert("Failed to save. Check permissions.");
    }
  }, []);

  return (
    <div style={{ padding: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '20px' }}>
        <h2>Infrastructure Dashboard</h2>
        <input 
          type="text" placeholder="Global Search..." value={globalSearch}
          onChange={(e) => setGlobalSearch(e.target.value)}
          style={{ padding: '8px', width: '300px' }}
        />
      </div>
      <div className="ag-theme-alpine" style={{ height: 'calc(100vh - 120px)', width: '100%' }}>
        <AgGridReact rowData={rowData} columnDefs={colDefs} pagination={true} paginationPageSize={50} onCellValueChanged={onCellValueChanged} />
      </div>
    </div>
  );
};
export default Dashboard;