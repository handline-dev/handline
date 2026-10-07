# Tellings the Go task writes leave out

None. What Python does after the commit of a task write to tell an agent (a typed say, a delivery, a push) is the supervisor's
telling layer (go/internal/supervisor), which the write methods call through taskwrites.Teller. A family branch that adds a write
method with a telling adds its line here only if it cannot call the Teller yet, and the cutover waits until the table is empty.

| Method | Python | What Go leaves out |
|---|---|---|
