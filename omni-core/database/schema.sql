-- Omni-Core ERP Database Schema for ACID Compliance

-- Table for Products
CREATE TABLE Products (
    ProductID INT PRIMARY KEY AUTO_INCREMENT,
    ProductName VARCHAR(255) NOT NULL,
    Description TEXT,
    Price DECIMAL(10, 2) NOT NULL,
    CreatedAt DATETIME DEFAULT CURRENT_TIMESTAMP,
    UpdatedAt DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Table for Sales
CREATE TABLE Sales (
    SaleID INT PRIMARY KEY AUTO_INCREMENT,
    ProductID INT NOT NULL,
    SaleDate DATETIME DEFAULT CURRENT_TIMESTAMP,
    Quantity INT NOT NULL,
    TotalAmount DECIMAL(10, 2) NOT NULL,
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID) ON DELETE CASCADE
);

-- Table for Items
CREATE TABLE Items (
    ItemID INT PRIMARY KEY AUTO_INCREMENT,
    SaleID INT NOT NULL,
    ProductID INT NOT NULL,
    Quantity INT NOT NULL,
    FOREIGN KEY (SaleID) REFERENCES Sales(SaleID) ON DELETE CASCADE,
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID) ON DELETE CASCADE
);

-- Table for Payments
CREATE TABLE Payments (
    PaymentID INT PRIMARY KEY AUTO_INCREMENT,
    SaleID INT NOT NULL,
    PaymentDate DATETIME DEFAULT CURRENT_TIMESTAMP,
    Amount DECIMAL(10, 2) NOT NULL,
    PaymentMethod ENUM('Credit Card', 'Cash', 'Bank Transfer'),
    FOREIGN KEY (SaleID) REFERENCES Sales(SaleID) ON DELETE CASCADE
);

-- Table for Audit Logs
CREATE TABLE AuditLogs (
    LogID INT PRIMARY KEY AUTO_INCREMENT,
    Action VARCHAR(255) NOT NULL,
    TableName VARCHAR(255) NOT NULL,
    RecordID INT NOT NULL,
    Timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX idx_product_name ON Products(ProductName);
CREATE INDEX idx_sale_date ON Sales(SaleDate);
CREATE INDEX idx_payment_date ON Payments(PaymentDate);