SELECT Profesor.Ime
FROM Profesor, Predaje
WHERE Profesor.SifP = Predaje.SifPro AND Profesor.SifD = 'RTI' AND Predaje.Semestar = 'L1994';
