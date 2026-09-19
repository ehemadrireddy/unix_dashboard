import React from 'react';

const CustomHeader = (props) => {
  const { displayName, type } = props;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
      <span>{displayName}</span>
      {type === 'manual' ? <span title="Manual Entry">🔵</span> : <span title="Auto-Fetched">⚙️</span>}
    </div>
  );
};
export default CustomHeader;