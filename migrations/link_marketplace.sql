-- Link Inventory and Orders Schema

CREATE TABLE IF NOT EXISTS link_inventory (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    domain VARCHAR(255) NOT NULL,
    niche VARCHAR(100) NOT NULL,
    da INTEGER NOT NULL DEFAULT 0,
    organic_traffic INTEGER NOT NULL DEFAULT 0,
    price DECIMAL(10, 2) NOT NULL,
    tat_days INTEGER NOT NULL DEFAULT 7,
    features JSONB DEFAULT '[]'::jsonb,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS link_orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    total_amount DECIMAL(10, 2) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS link_order_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID REFERENCES link_orders(id) ON DELETE CASCADE,
    link_id UUID REFERENCES link_inventory(id) ON DELETE SET NULL,
    target_url VARCHAR(255),
    anchor_text VARCHAR(255),
    price DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Seed some dummy data
INSERT INTO link_inventory (domain, niche, da, organic_traffic, price, tat_days, features) VALUES
('techcrunch-clone.com', 'Technology', 65, 150000, 350.00, 5, '["DoFollow", "Indexed"]'),
('saasgrowth-hub.io', 'SaaS', 52, 45000, 150.00, 7, '["DoFollow", "Contextual"]'),
('healthyliving-daily.org', 'Health', 48, 80000, 200.00, 10, '["DoFollow", "Homepage Link"]'),
('finance-insider.net', 'Finance', 70, 250000, 500.00, 3, '["DoFollow", "News Placement"]'),
('localbiz-directory.com', 'Local Business', 35, 12000, 50.00, 14, '["DoFollow", "Niche Edit"]'),
('ecommerce-trends.co', 'E-commerce', 58, 65000, 220.00, 7, '["DoFollow", "Product Review"]'),
('thetravel-bug.info', 'Travel', 42, 30000, 120.00, 7, '["DoFollow", "Guest Post"]'),
('fitness-freaks.com', 'Health', 55, 95000, 180.00, 5, '["DoFollow", "Contextual"]'),
('coding-ninjas.dev', 'Technology', 60, 110000, 280.00, 7, '["DoFollow", "Guest Post"]'),
('startup-founders.io', 'SaaS', 45, 25000, 100.00, 10, '["DoFollow", "Interview Link"]');
