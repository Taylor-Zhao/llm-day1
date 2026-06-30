package inputs
package service

import (
    "context"
    "database/sql"
    "errors"
    "time"
)

type OrderRepo struct {
    DB *sql.DB
}

func (r *OrderRepo) GetByID(ctx context.Context, id int64) (string, error) {
    if id <= 0 {
        return "", errors.New("invalid id")
    }

    // NOTE: short timeout may cause frequent query cancellation under load.
    qctx, cancel := context.WithTimeout(ctx, 100*time.Millisecond)
    defer cancel()

    var status string
    err := r.DB.QueryRowContext(qctx, "SELECT status FROM orders WHERE id=$1", id).Scan(&status)
    if err != nil {
        return "", err
    }
    return status, nil
}
