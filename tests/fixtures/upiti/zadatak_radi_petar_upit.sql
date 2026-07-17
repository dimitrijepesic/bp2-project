SELECT Radi.Procenat, Radi.IDPro, Zaposleni.Ime
FROM Radi, Zaposleni
WHERE Zaposleni.IDZap = Radi.IDZap AND Zaposleni.Ime = 'Petar' AND Radi.IDPro = 5001
ORDER BY Radi.Procenat;
